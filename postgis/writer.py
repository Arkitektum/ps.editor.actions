"""Write a PostGIS DDL script that materialises a feature catalogue's data model.

Given the assembled feature-type dicts (the same structure produced by the OGC/XMI/
GeoPackage loaders), this renders a plain SQL script -- ``CREATE TABLE`` and friends,
no data rows except code-list values -- that sets up an empty PostGIS database for
the model. Nothing connects to a database, so there is no driver dependency.

The table layout follows the Gistools PostGIS convention that the GeoPackage writer
also follows, so one ldproxy provider structure fits both:

* every table has a synthetic integer primary key ``objid``;
* inherited attributes are materialised in the concrete table, and abstract types
  get no table;
* single-valued data types are flattened into ``<attribute>_<field>`` columns;
* attributes that may repeat get a child table ``<table>_<attribute>`` with a
  foreign key back to the owner.

Where PostGIS can say more than GeoPackage, it does: a class with several
geometries keeps them as several geometry columns in one table (no split into
``_flate``/``_punkt`` types), code lists become lookup tables referenced by a
foreign key, enumerations become ``CHECK`` constraints, and associations become
foreign keys or join tables depending on their multiplicities.

Table and column names are lowercased and transliterated to plain ASCII
(``æ``→``ae``, ``ø``→``oe``, ``å``→``aa``), as in the Gistools PostGIS generator.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from geopackage.writer import (
    _GM_GEOM,
    _GPKG_GEOM,
    _SOSI_GEOM,
    PRIMARY_KEY_COLUMN,
    CodeListResolver,
    _effective_attributes,
    _epsg_code,
    _geometry_column_name,
    _is_multivalued,
    _memoize_resolver,
)

# Column holding the model's class name, as in the Gistools PostGIS generator. It
# tells rows apart once inheritance has folded several classes into one table.
OBJECT_TYPE_COLUMN = "objtype"

# Columns of a code-list lookup table, as in the Gistools PostGIS generator.
CODELIST_KEY_COLUMN = "identifier"
CODELIST_DESCRIPTION_COLUMN = "description"

# PostgreSQL silently truncates identifiers longer than this (NAMEDATALEN - 1), so
# two long names could end up as the same identifier.
_MAX_IDENTIFIER_LENGTH = 63

# Feature-catalogue attribute type -> PostgreSQL column type.
_COLUMN_TYPE = {
    "integer": "integer",
    "int": "integer",
    "string": "text",
    "characterstring": "text",
    "charactervalue": "text",
    "text": "text",
    "url": "text",
    "uri": "text",
    "link": "text",
    "number": "double precision",
    "real": "double precision",
    "double": "double precision",
    "decimal": "double precision",
    "float": "double precision",
    "boolean": "boolean",
    "bool": "boolean",
    "date": "date",
    "datetime": "timestamp with time zone",
    "dateandtime": "timestamp with time zone",
    "timestamp": "timestamp with time zone",
    "binary": "bytea",
}

# GeoPackage geometry type name (the vocabulary of the shared geometry maps) ->
# PostGIS geometry type modifier.
_POSTGIS_GEOMETRY = {
    "POINT": "Point",
    "MULTIPOINT": "MultiPoint",
    "LINESTRING": "LineString",
    "MULTILINESTRING": "MultiLineString",
    "POLYGON": "Polygon",
    "MULTIPOLYGON": "MultiPolygon",
    "GEOMETRY": "Geometry",
    "GEOMETRYCOLLECTION": "GeometryCollection",
}

_TRANSLITERATION = str.maketrans({"æ": "ae", "ø": "oe", "å": "aa"})

# Output order of the table kinds, so lookup tables come first and the tables that
# hang off feature tables come after them.
_KIND_ORDER = {"codelist": 0, "feature": 1, "attribute": 2, "association": 3}


# --------------------------------------------------------------------------- #
# Names and literals
# --------------------------------------------------------------------------- #


def _shorten(name: str) -> str:
    """Keep a name within PostgreSQL's identifier limit, deterministically.

    Truncation alone would map two long names with a common prefix onto the same
    identifier, so the tail is replaced by a hash of the full name.
    """
    if len(name) <= _MAX_IDENTIFIER_LENGTH:
        return name
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"{name[: _MAX_IDENTIFIER_LENGTH - 9].rstrip('_')}_{digest}"


def pg_name(text: Any) -> str:
    """Lowercase ASCII identifier for a model name.

    ``"Dyrkbar jord"`` -> ``dyrkbar_jord``, ``"Høyde"`` -> ``hoeyde``,
    ``"identifikasjon_lokalId"`` -> ``identifikasjon_lokalid``.
    """
    lowered = str(text or "").strip().lower().translate(_TRANSLITERATION)
    ascii_text = unicodedata.normalize("NFKD", lowered).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^a-z0-9_]+", "_", ascii_text)
    name = re.sub(r"_+", "_", name).strip("_")
    return _shorten(name or "unnamed")


def _compose(*parts: str) -> str:
    """Join already-sanitised name parts, keeping the result within the limit."""
    return _shorten("_".join(part for part in parts if part))


def _q(identifier: str) -> str:
    """Quote an identifier. Names are sanitised already, but quoting keeps words
    that PostgreSQL reserves (``user``, ``order``, ``group``) usable as names."""
    return '"' + identifier.replace('"', '""') + '"'


def _literal(value: Any) -> str:
    """SQL string literal (standard_conforming_strings, the default since 9.1)."""
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _lower_bound(cardinality: Any) -> int | None:
    text = str(cardinality or "").strip().replace(" ", "")
    if not text:
        return None
    lower = text.split("..", 1)[0]
    return int(lower) if lower.isdigit() else None


def _is_required(cardinality: Any) -> bool:
    lower = _lower_bound(cardinality)
    return lower is not None and lower >= 1


def _at_most_one(cardinality: Any) -> bool:
    """True only for a known multiplicity whose upper bound is 1. An unknown one
    is not assumed to be single-valued."""
    return bool(str(cardinality or "").strip()) and not _is_multivalued(cardinality)


# --------------------------------------------------------------------------- #
# Table model
# --------------------------------------------------------------------------- #


@dataclass
class Column:
    name: str
    sql_type: str
    not_null: bool = False
    identity: bool = False
    primary_key: bool = False
    default: str | None = None  # SQL expression
    comment: str | None = None
    geometry: bool = False
    check_values: list[str] | None = None


@dataclass
class ForeignKey:
    table: str
    column: str
    ref_table: str
    ref_column: str
    on_delete: str = "NO ACTION"


@dataclass
class Table:
    name: str
    kind: str  # codelist | feature | attribute | association
    comment: str | None = None
    columns: list[Column] = field(default_factory=list)
    unique: list[list[str]] = field(default_factory=list)
    rows: list[tuple[str, str | None]] = field(default_factory=list)

    def add_column(self, column: Column) -> Column:
        """Add a column, renaming it when a sanitised name is already taken
        (``Høyde`` and ``hoeyde`` both become ``hoeyde``)."""
        taken = {existing.name for existing in self.columns}
        base = column.name
        suffix = 2
        while column.name in taken:
            column.name = _compose(base, str(suffix))
            suffix += 1
        self.columns.append(column)
        return column

    def has_column(self, name: str) -> bool:
        return any(column.name == name for column in self.columns)


@dataclass
class PostgisModel:
    tables: list[Table] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)

    def table(self, name: str) -> Table | None:
        return next((table for table in self.tables if table.name == name), None)


def _primary_key_column() -> Column:
    return Column(
        PRIMARY_KEY_COLUMN, "integer", not_null=True, identity=True, primary_key=True
    )


def _code_values(items: Any) -> list[tuple[str, str | None]]:
    values: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        if value is None or str(value) == "" or str(value) in seen:
            continue
        seen.add(str(value))
        label = item.get("label")
        values.append((str(value), str(label) if label else None))
    return values


class _ModelBuilder:
    def __init__(
        self,
        feature_types: list[dict[str, Any]],
        *,
        default_srid: int,
        allow_z: bool,
        object_type_column: bool,
        codelist_resolver: CodeListResolver | None,
    ) -> None:
        self.feature_types = [ft for ft in feature_types if isinstance(ft, dict)]
        self.default_srid = default_srid
        self.allow_z = allow_z
        self.object_type_column = object_type_column
        self.resolver = codelist_resolver
        self.model = PostgisModel()
        self.by_name = {
            ft["name"]: ft for ft in self.feature_types if isinstance(ft.get("name"), str)
        }
        self.table_for_type: dict[str, str] = {}
        self.codelists: dict[str, Table] = {}
        self.pending_children: list[tuple[Table, str, dict[str, Any], int]] = []
        self.subtypes: dict[str, list[str]] = {}
        for ft in self.feature_types:
            relationships = ft.get("relationships")
            parents = relationships.get("inheritance", []) if isinstance(relationships, dict) else []
            for parent in parents:
                if isinstance(parent, str):
                    self.subtypes.setdefault(parent, []).append(ft["name"])

    # -- tables ----------------------------------------------------------- #

    def _unique_table_name(self, base: str) -> str:
        taken = {table.name for table in self.model.tables} | set(self.table_for_type.values())
        name = base
        suffix = 2
        while name in taken:
            name = _compose(base, str(suffix))
            suffix += 1
        return name

    def _add_table(self, table: Table) -> Table:
        self.model.tables.append(table)
        return table

    def build(self) -> PostgisModel:
        # Feature tables claim their names first, so a lookup or child table never
        # takes the name a feature type would have had.
        concrete = [
            ft
            for ft in self.feature_types
            if isinstance(ft.get("name"), str) and ft["name"].strip() and ft.get("abstract") is not True
        ]
        for ft in concrete:
            self.table_for_type[ft["name"]] = self._unique_table_name(pg_name(ft["name"]))
        for ft in concrete:
            self._build_feature_table(ft)
        while self.pending_children:
            owner, path, attribute, srid = self.pending_children.pop(0)
            self._build_child_table(owner, path, attribute, srid)
        self._build_associations()
        self.model.tables.sort(key=lambda table: _KIND_ORDER[table.kind])
        return self.model

    def _geometry_type(self, kind: str, srid: int) -> str:
        name = _POSTGIS_GEOMETRY.get(kind, "Geometry")
        return f"geometry({name}{'Z' if self.allow_z else ''}, {srid})"

    def _build_feature_table(self, ft: dict[str, Any]) -> None:
        table = self._add_table(
            Table(self.table_for_type[ft["name"]], "feature", comment=ft.get("description") or None)
        )
        table.add_column(_primary_key_column())
        attributes = _effective_attributes(ft, self.by_name)
        if self.object_type_column and not any(
            isinstance(a, dict) and pg_name(a.get("name")) == OBJECT_TYPE_COLUMN for a in attributes
        ):
            table.add_column(
                Column(OBJECT_TYPE_COLUMN, "text", not_null=True, default=_literal(ft["name"]))
            )

        srid = self.default_srid
        geometry = ft.get("geometry")
        if isinstance(geometry, dict):
            srid = (
                _epsg_code(geometry.get("storageCrs"))
                or _epsg_code(geometry.get("crs"))
                or self.default_srid
            )
            kind = _GPKG_GEOM.get(str(geometry.get("type") or "").strip().lower(), "GEOMETRY")
            table.add_column(
                Column(pg_name(_geometry_column_name(ft)), self._geometry_type(kind, srid), geometry=True)
            )

        self._add_attributes(table, attributes, prefix="", required=True, srid=srid)

    # -- attributes ------------------------------------------------------- #

    def _add_attributes(
        self,
        table: Table,
        attributes: list[Any],
        *,
        prefix: str,
        required: bool,
        srid: int,
    ) -> None:
        for attribute in attributes:
            if not isinstance(attribute, dict):
                continue
            name = attribute.get("name")
            if not isinstance(name, str) or not name:
                continue
            path = f"{prefix}{name}"
            cardinality = attribute.get("cardinality")

            if _is_multivalued(cardinality):
                self.pending_children.append((table, path, attribute, srid))
                continue

            # A flattened field is only mandatory when every level above it is.
            attribute_required = required and _is_required(cardinality)
            nested = attribute.get("attributes")
            if isinstance(nested, list) and nested:
                self._add_attributes(
                    table, nested, prefix=f"{path}_", required=attribute_required, srid=srid
                )
                continue
            self._add_value_column(table, path, attribute, required=attribute_required, srid=srid)

    def _add_value_column(
        self,
        table: Table,
        path: str,
        attribute: dict[str, Any],
        *,
        required: bool,
        srid: int,
    ) -> None:
        name = pg_name(path)
        if name in (PRIMARY_KEY_COLUMN, OBJECT_TYPE_COLUMN) and table.has_column(name):
            return  # the synthetic column already stands for it
        raw_type = str(attribute.get("type") or "").strip()
        low = raw_type.lower()
        description = attribute.get("description") or None

        geometry_kind = _GM_GEOM.get(low) or _SOSI_GEOM.get(low)
        if geometry_kind is None and low.startswith("gm_"):
            geometry_kind = "GEOMETRY"
        if geometry_kind:
            column = Column(
                name,
                self._geometry_type(geometry_kind, srid),
                not_null=required,
                geometry=True,
                comment=description,
            )
            # A geometry dict and a geometry attribute can describe the same property.
            if not any(c.name == name and c.geometry for c in table.columns):
                table.add_column(column)
            return

        column = table.add_column(
            Column(name, _COLUMN_TYPE.get(low, "text"), not_null=required, comment=description)
        )
        value_domain = attribute.get("valueDomain")
        if isinstance(value_domain, dict):
            self._apply_value_domain(table, column, raw_type, value_domain)

    def _apply_value_domain(
        self, table: Table, column: Column, type_name: str, value_domain: dict[str, Any]
    ) -> None:
        code_list = value_domain.get("codeList")
        values = _code_values(value_domain.get("listedValues"))
        if not values and code_list and self.resolver:
            values = _code_values(self.resolver(str(code_list)))

        if code_list:
            # Recorded either way: dropping it would lose the link to the register.
            note = f"Kodeliste: {code_list}"
            column.comment = f"{column.comment}\n\n{note}" if column.comment else note
        if not values:
            return

        column.sql_type = "text"  # codes are compared as text, whatever they look like
        if str(value_domain.get("kind") or "").strip().lower() == "enumeration":
            # A closed <<enumeration>> has no register to grow from, so the values
            # belong in the table definition.
            column.check_values = [value for value, _ in values]
            return

        lookup = self._codelist_table(type_name, table, column, values, value_domain)
        self.model.foreign_keys.append(
            ForeignKey(table.name, column.name, lookup.name, CODELIST_KEY_COLUMN)
        )

    def _codelist_table(
        self,
        type_name: str,
        owner: Table,
        column: Column,
        values: list[tuple[str, str | None]],
        value_domain: dict[str, Any],
    ) -> Table:
        """One lookup table per code list, shared by every attribute that uses it.

        The model's type name identifies the code list. Sources that carry no type
        name (OGC API, GeoPackage) name the lookup table after the column instead.
        """
        generic = not type_name or type_name.lower() in _COLUMN_TYPE
        key = f"{owner.name}.{column.name}" if generic else type_name
        existing = self.codelists.get(key)
        if existing is not None and existing.rows == values:
            return existing
        if existing is not None:
            # Same type name, different values: keep both rather than guess.
            key = f"{key}@{owner.name}.{column.name}"
        base = _compose(owner.name, column.name) if generic else pg_name(type_name)
        comment = value_domain.get("definition") or None
        code_list = value_domain.get("codeList")
        if code_list:
            comment = f"{comment}\n\nKodeliste: {code_list}" if comment else f"Kodeliste: {code_list}"
        table = self._add_table(Table(self._unique_table_name(base), "codelist", comment=comment))
        table.add_column(Column(CODELIST_KEY_COLUMN, "text", not_null=True, primary_key=True))
        table.add_column(Column(CODELIST_DESCRIPTION_COLUMN, "text"))
        table.rows = values
        self.codelists[key] = table
        return table

    def _build_child_table(
        self, owner: Table, path: str, attribute: dict[str, Any], srid: int
    ) -> None:
        """Give a repeating attribute its own table, one row per value.

        The rows belong to the owner, so they are deleted with it.
        """
        child = self._add_table(
            Table(
                self._unique_table_name(_compose(owner.name, pg_name(path))),
                "attribute",
                comment=attribute.get("description") or None,
            )
        )
        child.add_column(_primary_key_column())
        reference = child.add_column(Column(_compose(owner.name, "fk"), "integer", not_null=True))
        self.model.foreign_keys.append(
            ForeignKey(child.name, reference.name, owner.name, PRIMARY_KEY_COLUMN, on_delete="CASCADE")
        )

        nested = attribute.get("attributes")
        if isinstance(nested, list) and nested:
            self._add_attributes(child, nested, prefix="", required=True, srid=srid)
        else:
            # The outer multiplicity is carried by the relation, so each row holds
            # exactly one value, in a column named after the attribute itself.
            single = dict(attribute)
            single["cardinality"] = "1"
            self._add_value_column(child, str(attribute["name"]), single, required=True, srid=srid)

    # -- associations ----------------------------------------------------- #

    def _realisations(self, type_name: str, _seen: set[str] | None = None) -> list[str]:
        """Tables that hold instances of ``type_name``: its own, if it is concrete,
        and those of every concrete subtype."""
        seen = _seen if _seen is not None else set()
        if type_name in seen:
            return []
        seen.add(type_name)
        tables = [self.table_for_type[type_name]] if type_name in self.table_for_type else []
        for subtype in self.subtypes.get(type_name, []):
            tables.extend(
                table for table in self._realisations(subtype, seen) if table not in tables
            )
        return tables

    def _build_associations(self) -> None:
        seen: set[tuple[tuple[str, str], ...]] = set()
        for ft in self.feature_types:
            source = ft.get("name")
            relationships = ft.get("relationships")
            associations = (
                relationships.get("associations") if isinstance(relationships, dict) else None
            )
            if not isinstance(source, str) or not isinstance(associations, list):
                continue
            for association in associations:
                if not isinstance(association, dict):
                    continue
                target = association.get("target")
                if not isinstance(target, str) or target not in self.by_name:
                    continue  # external or unknown class: nothing to reference
                source_role = str(association.get("sourceRole") or "")
                target_role = str(association.get("role") or "")
                # A two-way navigable association is listed from both ends.
                key = tuple(sorted([(source, source_role), (target, target_role)]))
                if key in seen:
                    continue
                seen.add(key)

                source_tables = self._realisations(source)
                target_tables = self._realisations(target)
                if not source_tables or not target_tables:
                    continue

                if _at_most_one(association.get("cardinality")):
                    for table in source_tables:
                        self._add_references(table, target_role, target_tables)
                elif _at_most_one(association.get("sourceCardinality")):
                    for table in target_tables:
                        self._add_references(table, source_role, source_tables)
                else:
                    # Many on both ends -- or a near end nobody recorded, where only
                    # a join table is safe.
                    for a in source_tables:
                        for b in target_tables:
                            self._add_join_table(a, source_role, b, target_role)

    def _add_references(self, table_name: str, role: str, referenced: list[str]) -> None:
        """Foreign-key column(s) on ``table_name``. An association to a type with
        several concrete subtypes needs one column per subtype table."""
        table = self.model.table(table_name)
        if table is None:
            return
        for ref_table in referenced:
            if role:
                base = _compose(pg_name(role), ref_table) if len(referenced) > 1 else pg_name(role)
            else:
                base = ref_table
            column = table.add_column(Column(_compose(base, "fk"), "integer"))
            self.model.foreign_keys.append(
                ForeignKey(table.name, column.name, ref_table, PRIMARY_KEY_COLUMN)
            )

    def _add_join_table(self, a: str, a_role: str, b: str, b_role: str) -> None:
        join = self._add_table(
            Table(
                self._unique_table_name(_compose(a, pg_name(b_role) if b_role else b)),
                "association",
            )
        )
        # A separate primary key, as ShapeChange's ldproxy target expects
        # (rule-ldp2-all-associativeTablesWithSeparatePkField).
        join.add_column(_primary_key_column())
        if a == b:  # self-association: the roles tell the two ends apart
            a_base, b_base = pg_name(a_role) if a_role else a, pg_name(b_role) if b_role else b
        else:
            a_base, b_base = a, b
        a_column = join.add_column(Column(_compose(a_base, "fk"), "integer", not_null=True))
        b_column = join.add_column(Column(_compose(b_base, "fk"), "integer", not_null=True))
        join.unique.append([a_column.name, b_column.name])
        for column, ref_table in ((a_column, a), (b_column, b)):
            self.model.foreign_keys.append(
                ForeignKey(join.name, column.name, ref_table, PRIMARY_KEY_COLUMN, on_delete="CASCADE")
            )


def build_postgis_model(
    feature_types: list[dict[str, Any]],
    *,
    default_srid: int = 25833,
    allow_z: bool = False,
    object_type_column: bool = True,
    codelist_resolver: CodeListResolver | None = None,
) -> PostgisModel:
    """Build the table model for ``feature_types`` without rendering it."""
    resolver = _memoize_resolver(codelist_resolver) if codelist_resolver else None
    return _ModelBuilder(
        feature_types,
        default_srid=default_srid,
        allow_z=allow_z,
        object_type_column=object_type_column,
        codelist_resolver=resolver,
    ).build()


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


class _Renderer:
    def __init__(self, model: PostgisModel, *, schema: str | None) -> None:
        self.model = model
        self.schema = pg_name(schema) if schema else None
        # Index names share the schema's namespace with tables.
        self.relation_names = {table.name for table in model.tables}

    def qualified(self, table: str) -> str:
        return f"{_q(self.schema)}.{_q(table)}" if self.schema else _q(table)

    def relation_name(self, base: str) -> str:
        name = base
        suffix = 2
        while name in self.relation_names:
            name = _compose(base, str(suffix))
            suffix += 1
        self.relation_names.add(name)
        return name

    def create_table(self, table: Table) -> list[str]:
        parts: list[str] = []
        for column in table.columns:
            piece = f"{_q(column.name)} {column.sql_type}"
            if column.identity:
                piece += " GENERATED BY DEFAULT AS IDENTITY"
            if column.primary_key:
                piece += " PRIMARY KEY"
            elif column.not_null:
                piece += " NOT NULL"
            if column.default is not None:
                piece += f" DEFAULT {column.default}"
            parts.append(piece)
        for column in table.columns:
            if column.check_values:
                listed = ", ".join(_literal(value) for value in column.check_values)
                parts.append(
                    f"CONSTRAINT {_q(_compose(table.name, column.name, 'check'))} "
                    f"CHECK ({_q(column.name)} IN ({listed}))"
                )
        for columns in table.unique:
            name = self.relation_name(_compose(table.name, "key"))
            parts.append(
                f"CONSTRAINT {_q(name)} UNIQUE ({', '.join(_q(column) for column in columns)})"
            )
        lines = [f"CREATE TABLE {self.qualified(table.name)} ("]
        lines.append(",\n".join(f"  {part}" for part in parts))
        lines.append(");")
        statements = ["\n".join(lines)]
        if table.rows:
            values = ",\n".join(
                f"  ({_literal(value)}, {_literal(label)})" for value, label in table.rows
            )
            statements.append(
                f"INSERT INTO {self.qualified(table.name)} "
                f"({_q(CODELIST_KEY_COLUMN)}, {_q(CODELIST_DESCRIPTION_COLUMN)}) VALUES\n{values};"
            )
        return statements

    def render(self, *, owner: str | None, read_role: str | None) -> str:
        out: list[str] = [
            "-- PostGIS schema generated by ps.editor.actions from the feature catalogue.",
            "-- Structure only: no data apart from code-list values.",
            "",
            "BEGIN;",
            "",
            "CREATE EXTENSION IF NOT EXISTS postgis;",
        ]
        if self.schema:
            statement = f"CREATE SCHEMA IF NOT EXISTS {_q(self.schema)}"
            if owner:
                statement += f" AUTHORIZATION {_q(owner)}"
            out.append(statement + ";")
        out.append("")

        for table in self.model.tables:
            out.extend(self.create_table(table))
            out.append("")

        foreign_keys = self.model.foreign_keys
        if foreign_keys:
            names: set[tuple[str, str]] = set()
            for fk in foreign_keys:
                base = _compose(fk.table, fk.column, "fkey")
                name, suffix = base, 2
                while (fk.table, name) in names:
                    name, suffix = _compose(base, str(suffix)), suffix + 1
                names.add((fk.table, name))
                out.append(
                    f"ALTER TABLE {self.qualified(fk.table)} ADD CONSTRAINT {_q(name)} "
                    f"FOREIGN KEY ({_q(fk.column)}) "
                    f"REFERENCES {self.qualified(fk.ref_table)} ({_q(fk.ref_column)}) "
                    f"ON DELETE {fk.on_delete};"
                )
            out.append("")

        indexes: list[str] = []
        for table in self.model.tables:
            for column in table.columns:
                if column.geometry:
                    name = self.relation_name(_compose(table.name, column.name, "gist"))
                    indexes.append(
                        f"CREATE INDEX {_q(name)} ON {self.qualified(table.name)} "
                        f"USING GIST ({_q(column.name)});"
                    )
        # PostgreSQL does not index the referencing side of a foreign key itself,
        # unless a UNIQUE constraint happens to lead with the column.
        leading = {
            (table.name, columns[0]) for table in self.model.tables for columns in table.unique
        }
        for fk in foreign_keys:
            if (fk.table, fk.column) in leading:
                continue
            name = self.relation_name(_compose(fk.table, fk.column, "idx"))
            indexes.append(
                f"CREATE INDEX {_q(name)} ON {self.qualified(fk.table)} ({_q(fk.column)});"
            )
        if indexes:
            out.extend(indexes)
            out.append("")

        comments: list[str] = []
        for table in self.model.tables:
            if table.comment:
                comments.append(
                    f"COMMENT ON TABLE {self.qualified(table.name)} IS {_literal(table.comment)};"
                )
            for column in table.columns:
                if column.comment:
                    comments.append(
                        f"COMMENT ON COLUMN {self.qualified(table.name)}.{_q(column.name)} "
                        f"IS {_literal(column.comment)};"
                    )
        if comments:
            out.extend(comments)
            out.append("")

        if read_role:
            schema = _q(self.schema) if self.schema else "public"
            out.append(f"GRANT USAGE ON SCHEMA {schema} TO {_q(read_role)};")
            out.append(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {_q(read_role)};")
            out.append("")

        out.append("COMMIT;")
        return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# Public entry
# --------------------------------------------------------------------------- #


def build_postgis_ddl(
    feature_types: list[dict[str, Any]],
    *,
    schema: str | None = None,
    default_srid: int = 25833,
    allow_z: bool = False,
    object_type_column: bool = True,
    owner: str | None = None,
    read_role: str | None = None,
    codelist_resolver: CodeListResolver | None = None,
) -> str:
    """Render the PostGIS DDL script for ``feature_types``.

    ``schema`` qualifies every table (unqualified tables land in the search path,
    normally ``public``); ``owner`` becomes the schema's ``AUTHORIZATION``, and
    ``read_role`` is granted read access to the schema's tables. Geometry columns
    use the feature type's storage CRS, else ``default_srid``. External code lists
    are resolved to lookup tables when ``codelist_resolver`` is given (pass
    :func:`geopackage.writer._fetch_geonorge_codelist` for the Geonorge register);
    unresolved ones stay plain text columns with the register URL in a comment.
    """
    model = build_postgis_model(
        feature_types,
        default_srid=default_srid,
        allow_z=allow_z,
        object_type_column=object_type_column,
        codelist_resolver=codelist_resolver,
    )
    return _Renderer(model, schema=schema).render(owner=owner, read_role=read_role)


def write_postgis_ddl(
    feature_types: list[dict[str, Any]],
    path: str | Path,
    **options: Any,
) -> Path:
    """Write :func:`build_postgis_ddl`'s script to ``path`` and return the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_postgis_ddl(feature_types, **options), encoding="utf-8")
    return path
