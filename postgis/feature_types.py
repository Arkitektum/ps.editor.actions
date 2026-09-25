"""Load feature types from a PostGIS DDL script (``.sql``).

The script is read, not executed: :mod:`postgis.sql` pulls the tables, columns,
constraints, comments and code-list rows out of it, and this module turns them
into the feature-type dicts the other loaders produce, so the downstream
feature-catalogue rendering stays generator-agnostic.

Scripts written by :mod:`postgis.writer` carry the model in their comments (the
``@ps`` line), and read back into exactly the feature catalogue they were written
from: original names, flattened data types, abstract supertypes, code-list
details. Any other script -- ``pg_dump --schema-only``, the Gistools PostGIS
generator -- is read from its structure, following the same conventions in
reverse:

* ``identifier``/``description`` tables are code lists, and a column referencing
  one gets their rows as ``listedValues``; ``CHECK (col IN (…))`` is an enumeration;
* a table whose one required foreign key cascades from an owner (or is named
  ``<owner>_…``) holds a repeating attribute of the owner;
* a table of two foreign keys that are unique together is a many-to-many
  association, and any other foreign key to a feature table an association;
* ``objid`` and ``objtype`` are synthetic, but ``objtype``'s default is the
  class's model name;
* one geometry column becomes ``geometry``, several become ``GM_*`` attributes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from geopackage.feature_types import _GEOMETRY_MAP
from geopackage.writer import PRIMARY_KEY_COLUMN
from postgis.sql import SqlColumn, SqlForeignKey, SqlSchema, SqlTable, Token, literal, parse_sql
from postgis.writer import (
    CODELIST_DESCRIPTION_COLUMN,
    CODELIST_KEY_COLUMN,
    METADATA_INDEX,
    METADATA_PREFIX,
    METADATA_ROWS,
    OBJECT_TYPE_COLUMN,
    listed_values_from_rows,
    pg_name,
)

try:  # requests is installed by the action; guard so the module imports in tests
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

__all__ = ["feature_types_from_sql", "load_feature_types_from_postgis"]

# PostgreSQL column type -> feature-catalogue attribute type.
_TYPE_MAP = {
    **dict.fromkeys(
        ("integer", "int", "int2", "int4", "int8", "smallint", "bigint", "serial", "bigserial", "smallserial"),
        "integer",
    ),
    **dict.fromkeys(
        ("double precision", "real", "float", "float4", "float8", "numeric", "decimal"), "number"
    ),
    **dict.fromkeys(("boolean", "bool"), "boolean"),
    "date": "date",
    **dict.fromkeys(
        ("timestamp", "timestamptz", "timestamp with time zone", "timestamp without time zone"),
        "dateTime",
    ),
    "bytea": "binary",
}

# Geometry type -> ISO 19107 type, for a table with several geometry columns.
_GM_TYPE = {
    "POINT": "GM_Point",
    "MULTIPOINT": "GM_MultiPoint",
    "LINESTRING": "GM_Curve",
    "MULTILINESTRING": "GM_MultiCurve",
    "POLYGON": "GM_Surface",
    "MULTIPOLYGON": "GM_MultiSurface",
}

_SYNTHETIC_KEYS = (PRIMARY_KEY_COLUMN, "fid")
_CODE_LIST_NOTE = re.compile(r"(?:^|\n\n)Kodeliste:\s*(\S+)\s*$")


def split_comment(text: str | None) -> tuple[str | None, dict[str, Any] | None]:
    """A comment's human text and its ``@ps`` model metadata, if any."""
    if not text:
        return None, None
    marker = "\n\n" + METADATA_PREFIX
    if text.startswith(METADATA_PREFIX):
        human, payload = "", text[len(METADATA_PREFIX) :]
    elif marker in text:
        human, payload = text.rsplit(marker, 1)
    else:
        return text, None
    try:
        meta = json.loads(payload)
    except ValueError:
        return text, None
    if not isinstance(meta, dict):
        return text, None
    return (human or None), meta


def _strip_code_list_note(text: str | None, url: Any) -> str | None:
    """The description without the ``Kodeliste: <url>`` note the writer adds."""
    if not text or not url:
        return text
    note = f"Kodeliste: {url}"
    if text == note:
        return None
    if text.endswith("\n\n" + note):
        return text[: -len(note) - 2] or None
    return text


def _split_code_list_note(text: str | None) -> tuple[str | None, str | None]:
    """``(description, url)`` for a comment that may end in a ``Kodeliste:`` note."""
    if not text:
        return None, None
    match = _CODE_LIST_NOTE.search(text)
    if not match:
        return text, None
    return (text[: match.start()].strip() or None), match.group(1)


def _names_in(tokens: list[Token]) -> list[str]:
    return [token.value for token in tokens if token.kind in ("word", "ident")]


def _check_values(tokens: list[Token], columns: dict[str, SqlColumn]) -> tuple[str, list[str]] | None:
    """``(column, values)`` for ``col IN ('a', 'b')`` and PostgreSQL's own rewrite
    of it, ``col = ANY (ARRAY['a'::text, 'b'::text])``."""
    words = {token.value for token in tokens if token.kind == "word"}
    if not ({"in", "any"} & words) or {"or", "and", "not"} & words:
        return None
    names = [name for name in _names_in(tokens) if name in columns]
    values = [token.value for token in tokens if token.kind == "string"]
    if len(set(names)) != 1 or not values:
        return None
    return names[0], values


def _check_geometry(tokens: list[Token], function: str) -> tuple[str, Any] | None:
    """``(column, value)`` for ``st_srid(col) = 25833`` or
    ``geometrytype(col) = 'MULTIPOLYGON'`` -- the Gistools generator's way of
    constraining an untyped geometry column."""
    for index, token in enumerate(tokens):
        if token.is_word(function) and index + 2 < len(tokens) and tokens[index + 1].is_op("("):
            column = tokens[index + 2]
            rest = tokens[index + 3 :]
            for position, candidate in enumerate(rest):
                if candidate.is_op("=") and position + 1 < len(rest):
                    return column.value, literal(rest[position + 1 : position + 2])
    return None


def _read_source(path_or_url: str | Path, http_get: Any = None) -> str:
    local = Path(path_or_url)
    if local.exists():
        return local.read_text(encoding="utf-8-sig")
    text = str(path_or_url)
    if not re.match(r"https?://", text):
        raise FileNotFoundError(f"PostGIS script '{text}' not found.")
    if http_get is None:
        if requests is None:  # pragma: no cover
            raise RuntimeError("The 'requests' package is required to download PostGIS scripts.")
        http_get = lambda url: requests.get(url, timeout=120)  # noqa: E731
    response = http_get(text)
    status = getattr(response, "status_code", 200)
    if status >= 400:
        raise RuntimeError(f"Request to download '{text}' failed with status code {status}.")
    # Decoded here rather than trusting requests' guess, which is ISO-8859-1 for
    # a text response without a charset and would garble æ/ø/å.
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content.decode("utf-8-sig")
    return response.text


class _Reader:
    def __init__(self, sql: SqlSchema, tables: list[SqlTable]) -> None:
        self.sql = sql
        self.tables = tables
        self.by_key = {table.key: table for table in tables}
        self.table_meta: dict[tuple, tuple[str | None, dict[str, Any] | None]] = {
            table.key: split_comment(table.comment) for table in tables
        }
        self.column_meta: dict[tuple, tuple[str | None, dict[str, Any] | None]] = {
            (table.key, name): split_comment(text)
            for table in tables
            for name, text in table.column_comments.items()
        }
        self.lookups = {table.key for table in tables if self._is_lookup(table)}

    # -- structure ---------------------------------------------------------- #

    @staticmethod
    def _is_lookup(table: SqlTable) -> bool:
        return (
            set(table.columns) == {CODELIST_KEY_COLUMN, CODELIST_DESCRIPTION_COLUMN}
            and table.primary_key == [CODELIST_KEY_COLUMN]
        )

    def target(self, table: SqlTable, fk: SqlForeignKey) -> SqlTable | None:
        """The table a foreign key references, among the tables being read."""
        schema = fk.ref_schema if fk.ref_schema is not None else table.schema
        found = self.by_key.get((schema, fk.ref_table))
        if found is None and fk.ref_schema is None:
            candidates = [t for t in self.tables if t.name == fk.ref_table]
            found = candidates[0] if len(candidates) == 1 else None
        return found

    def fk_of(self, table: SqlTable, column: str) -> tuple[SqlForeignKey, SqlTable] | None:
        for fk in table.foreign_keys:
            if fk.columns == [column]:
                referenced = self.target(table, fk)
                if referenced is not None:
                    return fk, referenced
        return None

    def geometry(self, table: SqlTable, column: SqlColumn) -> tuple[str, int | None] | None:
        """``(geometry type, srid)`` for a geometry column, else ``None``."""
        if column.type_name not in ("geometry", "geography"):
            return None
        kind, srid = "GEOMETRY", None
        if column.type_args:
            kind = re.sub(r"(ZM|Z|M)$", "", column.type_args[0].upper()) or "GEOMETRY"
            if len(column.type_args) > 1 and column.type_args[1].strip().isdigit():
                srid = int(column.type_args[1])
        elif column.type_name == "geography":
            srid = 4326
        hint = self.sql.geometry_columns.get((table.schema, table.name, column.name))
        if hint:
            kind, srid = str(hint[0]).upper(), hint[1] if isinstance(hint[1], int) else srid
        for check in table.checks:
            found = _check_geometry(check, "st_srid")
            if found and found[0] == column.name and isinstance(found[1], int):
                srid = found[1]
            found = _check_geometry(check, "geometrytype")
            if found and found[0] == column.name and isinstance(found[1], str):
                kind = found[1].upper()
        return kind, srid

    def check_values(self, table: SqlTable, column: str) -> list[str] | None:
        for check in table.checks:
            found = _check_values(check, table.columns)
            if found and found[0] == column:
                return found[1]
        return None

    def lookup_rows(self, lookup: SqlTable) -> list[dict[str, str]]:
        rows = [
            (str(row.get(CODELIST_KEY_COLUMN)), row.get(CODELIST_DESCRIPTION_COLUMN))
            for row in lookup.rows
            if row.get(CODELIST_KEY_COLUMN) is not None
        ]
        return listed_values_from_rows(
            [(value, str(label) if label is not None else None) for value, label in rows]
        )

    def values_for(self, table: SqlTable, column: str) -> list[dict[str, str]]:
        """The codes a column may hold: its lookup table's rows, or its CHECK."""
        reference = self.fk_of(table, column)
        if reference and reference[1].key in self.lookups:
            return self.lookup_rows(reference[1])
        return [{"value": value} for value in self.check_values(table, column) or []]

    def owner(self, table: SqlTable) -> tuple[str, SqlTable] | None:
        """``(fk column, owner)`` when ``table`` holds a repeating attribute."""
        references = [
            (fk, referenced)
            for fk in table.foreign_keys
            if len(fk.columns) == 1
            and (referenced := self.target(table, fk)) is not None
            and referenced.key not in self.lookups
        ]
        if len(references) != 1:
            return None
        fk, referenced = references[0]
        column = table.columns.get(fk.columns[0])
        if column is None or not column.not_null:
            return None
        if fk.on_delete == "CASCADE" or table.name.startswith(f"{referenced.name}_"):
            return fk.columns[0], referenced
        return None

    def join_ends(self, table: SqlTable) -> list[tuple[str, SqlTable]] | None:
        """The two ends of a many-to-many join table, else ``None``."""
        data = [name for name in table.columns if name not in self._synthetic(table)]
        if len(data) != 2:
            return None
        ends = []
        for name in data:
            reference = self.fk_of(table, name)
            if reference is None or reference[1].key in self.lookups:
                return None
            ends.append((name, reference[1]))
        together = sorted(data)
        if not any(sorted(u) == together for u in table.unique) and sorted(table.primary_key) != together:
            return None
        return ends

    @staticmethod
    def _synthetic(table: SqlTable) -> set[str]:
        names = set()
        if len(table.primary_key) == 1 and table.primary_key[0] in _SYNTHETIC_KEYS:
            names.add(table.primary_key[0])
        if OBJECT_TYPE_COLUMN in table.columns:
            names.add(OBJECT_TYPE_COLUMN)
        return names

    # -- reading ------------------------------------------------------------ #

    def read(self) -> list[dict[str, Any]]:
        types: dict[str, dict[str, Any]] = {}
        order: dict[str, int] = {}
        type_of_table: dict[tuple, str] = {}

        # Tables written with model metadata: the feature type comes back whole.
        for table in self.tables:
            human, meta = self.table_meta[table.key]
            if not meta or not isinstance(meta.get("type"), dict):
                continue
            recorded = dict(meta["type"])
            name = recorded.get("name")
            if not isinstance(name, str):
                continue
            if "description" not in recorded and human:
                recorded["description"] = human
            recorded["attributes"] = []
            types.setdefault(name, recorded)
            order.setdefault(name, meta.get(METADATA_INDEX, len(order)))
            type_of_table[table.key] = name
            for related in meta.get("related") or []:
                other = dict(related.get("type") or {})
                other_name = other.get("name")
                if isinstance(other_name, str) and other_name not in types:
                    other.setdefault("attributes", [])
                    types[other_name] = other
                    order[other_name] = related.get(METADATA_INDEX, len(order))

        # Every column that records its path -- in feature tables and in the
        # child tables of repeating attributes alike -- goes back where it was.
        for table in self.tables:
            for name in table.columns:
                human, meta = self.column_meta.get((table.key, name), (None, None))
                if not meta or not isinstance(meta.get("path"), list) or not meta["path"]:
                    continue
                owner = types.get(meta.get("from")) or types.get(type_of_table.get(table.key, ""))
                if owner is not None:
                    self._insert(owner, meta["path"], table, name, human)

        result = sorted(types.values(), key=lambda ft: order.get(ft["name"], 0))
        result.extend(self._read_structure(type_of_table))
        for feature_type in result:
            _finish(feature_type.get("attributes"))
        return result

    def _insert(
        self, feature_type: dict[str, Any], path: list[Any], table: SqlTable, column: str, text: str | None
    ) -> None:
        level = feature_type.setdefault("attributes", [])
        for segment in path[:-1]:
            node = _find(level, segment.get("name"))
            if node is None:
                node = dict(segment)
                node["attributes"] = []
                level.append(node)
            level = node.setdefault("attributes", [])
        leaf_segment = path[-1]
        if not isinstance(leaf_segment, dict) or _find(level, leaf_segment.get("name")) is not None:
            return  # the same inherited attribute, seen again in another subtype
        leaf = dict(leaf_segment)
        value_domain = leaf.get("valueDomain")
        if isinstance(value_domain, dict):
            value_domain = dict(value_domain)
            leaf["valueDomain"] = value_domain
            if value_domain.get("listedValues") == METADATA_ROWS:
                value_domain["listedValues"] = self.values_for(table, column)
        if "description" not in leaf:
            url = value_domain.get("codeList") if isinstance(value_domain, dict) else None
            description = _strip_code_list_note(text, url)
            if description:
                leaf["description"] = description
        level.append(leaf)

    # -- structure only ----------------------------------------------------- #

    def _read_structure(self, type_of_table: dict[tuple, str]) -> list[dict[str, Any]]:
        """Feature types for the tables that carry no model metadata."""
        children: dict[tuple, list[tuple[str, SqlTable]]] = {}
        joins: list[tuple[SqlTable, list[tuple[str, SqlTable]]]] = []
        plain: list[SqlTable] = []
        for table in self.tables:
            if table.key in self.lookups:
                continue
            owner = None if table.key in type_of_table else self.owner(table)
            ends = None if table.key in type_of_table else self.join_ends(table)
            if ends:
                joins.append((table, ends))
            elif owner:
                children.setdefault(owner[1].key, []).append((owner[0], table))
            elif table.key not in type_of_table:
                plain.append(table)

        names = dict(type_of_table)
        for table in plain:
            names[table.key] = self._model_name(table)

        feature_types: list[dict[str, Any]] = []
        for table in plain:
            feature_types.append(self._feature_type(table, names, children.get(table.key, [])))
        by_table = dict(zip((t.key for t in plain), feature_types))
        for table, ends in joins:
            (_, a), (_, b) = ends
            source = by_table.get(a.key)
            if source is None or b.key not in names:
                continue  # a table written with metadata declares its own associations
            role = table.name[len(a.name) + 1 :] if table.name.startswith(f"{a.name}_") else ""
            association: dict[str, Any] = {"target": names[b.key]}
            if role and role != b.name:
                association["role"] = role
            association["cardinality"] = "0..*"
            association["sourceCardinality"] = "0..*"
            source["relationships"]["associations"].append(association)
        return feature_types

    def _model_name(self, table: SqlTable) -> str:
        column = table.columns.get(OBJECT_TYPE_COLUMN)
        if column is not None and column.default:
            value = literal(column.default)
            if isinstance(value, str) and value:
                return value
        return table.name

    def _feature_type(
        self,
        table: SqlTable,
        names: dict[tuple, str],
        children: list[tuple[str, SqlTable]],
    ) -> dict[str, Any]:
        human, _ = self.table_meta[table.key]
        feature_type: dict[str, Any] = {"name": names[table.key], "description": human or ""}
        synthetic = self._synthetic(table)
        geometries = [
            (name, geometry)
            for name, column in table.columns.items()
            if (geometry := self.geometry(table, column)) is not None
        ]
        if len(geometries) == 1:
            name, (kind, srid) = geometries[0]
            geometry_type = _GEOMETRY_MAP.get(kind, "geometry")
            geometry: dict[str, Any] = {
                "itemType": "feature",
                "type": geometry_type,
                "format": geometry_type,
                "ogcRole": "primary-geometry",
            }
            if name != "geometry":
                geometry["name"] = name
            if srid:
                uri = f"http://www.opengis.net/def/crs/EPSG/0/{srid}"
                geometry["crs"] = [uri]
                geometry["storageCrs"] = uri
            feature_type["geometry"] = geometry
            synthetic.add(name)

        attributes: list[dict[str, Any]] = []
        associations: list[dict[str, Any]] = []
        for name, column in table.columns.items():
            if name in synthetic:
                continue
            reference = self.fk_of(table, name)
            if reference and reference[1].key not in self.lookups and reference[1].key in names:
                role = name[: -len("_fk")] if name.endswith("_fk") else name
                association = {"target": names[reference[1].key]}
                if role != reference[1].name:
                    association["role"] = role
                association["cardinality"] = "1" if column.not_null else "0..1"
                association["sourceCardinality"] = "0..*"
                associations.append(association)
                continue
            attributes.append(self._attribute(table, name, column))

        for fk_column, child in children:
            attributes.append(self._repeating_attribute(table, fk_column, child))

        feature_type["attributes"] = attributes
        inheritance = [
            names[parent.key]
            for schema, parent_name in table.inherits
            if (parent := self.sql.find(schema, parent_name)) is not None and parent.key in names
        ]
        feature_type["relationships"] = {"inheritance": inheritance, "associations": associations}
        return feature_type

    def _attribute(self, table: SqlTable, name: str, column: SqlColumn) -> dict[str, Any]:
        human, _ = self.column_meta.get((table.key, name), (None, None))
        description, code_list = _split_code_list_note(human)
        geometry = self.geometry(table, column)
        if geometry:
            attribute_type = _GM_TYPE.get(geometry[0], "GM_Object")
        else:
            attribute_type = _TYPE_MAP.get(column.type_name, "string")
        attribute: dict[str, Any] = {
            "name": name,
            "type": attribute_type,
            "cardinality": "0..*" if column.array else ("1" if column.not_null else "0..1"),
        }
        if description:
            attribute["description"] = description

        value_domain: dict[str, Any] = {}
        reference = self.fk_of(table, name)
        if reference and reference[1].key in self.lookups:
            lookup = reference[1]
            value_domain["listedValues"] = self.lookup_rows(lookup)
            definition, lookup_url = _split_code_list_note(split_comment(lookup.comment)[0])
            if definition:
                value_domain["definition"] = definition
            code_list = code_list or lookup_url
        else:
            values = self.check_values(table, name)
            if values:
                value_domain["kind"] = "enumeration"
                value_domain["listedValues"] = [{"value": value} for value in values]
        if code_list:
            value_domain["codeList"] = code_list
        if value_domain:
            attribute["valueDomain"] = value_domain
        return attribute

    def _repeating_attribute(self, owner: SqlTable, fk_column: str, child: SqlTable) -> dict[str, Any]:
        name = child.name[len(owner.name) + 1 :] if child.name.startswith(f"{owner.name}_") else child.name
        fields = [
            self._attribute(child, column_name, column)
            for column_name, column in child.columns.items()
            if column_name not in self._synthetic(child) and column_name != fk_column
        ]
        if len(fields) == 1:
            attribute = dict(fields[0])
            attribute["name"] = name
            attribute["cardinality"] = "0..*"
            return attribute
        human, _ = self.table_meta[child.key]
        attribute = {"name": name, "type": name, "cardinality": "0..*", "attributes": fields}
        if human:
            attribute["description"] = human
        return attribute


def _find(level: list[Any], name: Any) -> dict[str, Any] | None:
    return next((a for a in level if isinstance(a, dict) and a.get("name") == name), None)


def _finish(attributes: Any) -> None:
    """Restore the recorded order and drop the bookkeeping keys."""
    if not isinstance(attributes, list):
        return
    attributes.sort(key=lambda a: a.get(METADATA_INDEX, 0) if isinstance(a, dict) else 0)
    for attribute in attributes:
        if isinstance(attribute, dict):
            attribute.pop(METADATA_INDEX, None)
            _finish(attribute.get("attributes"))


def feature_types_from_sql(text: str, *, schema: str | None = None) -> list[dict[str, Any]]:
    """Read the feature types out of a DDL script's text.

    ``schema`` restricts the reading to the tables of one database schema; by
    default every table in the script is read.
    """
    parsed = parse_sql(text)
    # Old-style scripts add geometry columns with AddGeometryColumn after
    # CREATE TABLE, so the column is not in the table definition.
    for (table_schema, table_name, column_name), (kind, srid, _) in parsed.geometry_columns.items():
        table = parsed.tables.get((table_schema, table_name)) or parsed.find(None, table_name)
        if table is not None and column_name not in table.columns:
            table.columns[column_name] = SqlColumn(column_name, "geometry", [kind, str(srid)])

    # The writer sanitises the schema name like any other, so accept that form too.
    wanted = {schema, schema.lower(), pg_name(schema)} if schema else None
    tables = [t for t in parsed.tables.values() if wanted is None or t.schema in wanted]
    if not tables:
        where = f" in schema '{schema}'" if schema else ""
        raise RuntimeError(f"The script contains no CREATE TABLE statements{where}.")
    return _Reader(parsed, tables).read()


def load_feature_types_from_postgis(
    path_or_url: str | Path,
    *,
    schema: str | None = None,
    http_get: Any = None,
) -> list[dict[str, Any]]:
    """Read the data model (feature types) from a PostGIS DDL script.

    ``path_or_url`` is a local ``.sql`` file or an http(s) URL. ``schema`` picks
    one database schema when the script defines several. ``http_get`` is
    injectable for testing.
    """
    return feature_types_from_sql(_read_source(path_or_url, http_get), schema=schema)
