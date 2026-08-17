# ps.editor.actions - Generate Product Specifications

This repository provides a reusable GitHub Action that fetches metadata from Geonorge and feature catalogue information from an OGC API - Features endpoint to assemble a complete product specification package. The action downloads psdata JSON, builds feature catalogue caches (JSON, Markdown, PlantUML) and renders a Markdown specification using the bundled Handlebars-style template or a custom template you supply. 

## Usage

```yaml
jobs:
  produktspesifikasjon:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - id: prepare
        uses: arkitektum/ps.editor.actions@main
        with:
          metadata-id: 12345678-abcd-1234-abcd-1234567890ab
          ogc-feature-api: https://example.com/collections
          output-directory: produktspesifikasjon
          product-slug: mitt-produkt
          updated: 2025-01-01

      - name: Render UML to PNG
        if: steps.prepare.outputs.feature-catalogue-uml != ''
        run: |
          sudo apt-get update
          sudo apt-get install -y plantuml graphviz
          set -euo pipefail
          while IFS= read -r file; do
            output_dir="$(dirname "$file")"
            plantuml -tpng -output "$PWD/$output_dir" "$file"
          done < <(git ls-files '*.puml')

      - name: Derive PNG path
        if: steps.prepare.outputs.feature-catalogue-uml != ''
        id: feature-png
        run: |
          png="${{ steps.prepare.outputs.feature-catalogue-uml }}"
          png="${png%.puml}.png"
          echo "path=$png" >> "$GITHUB_OUTPUT"

      - id: assemble
        uses: arkitektum/ps.editor.actions/assemble@main
        with:
          psdata-path: ${{ steps.prepare.outputs.psdata-path }}
          output-path: ${{ steps.prepare.outputs.spec-markdown }}
          feature-catalogue-markdown: ${{ steps.prepare.outputs.feature-catalogue-markdown }}
          feature-catalogue-uml: ${{ steps.prepare.outputs.feature-catalogue-uml }}
          feature-catalogue-png: ${{ steps.feature-png.outputs.path }}
          updated: 2025-01-01
```

The first action fetches and prepares every artefact. The optional PlantUML step converts the diagram to PNG before the second action stitches everything into the final Markdown document.

## Actions

### Prepare artefacts (`arkitektum/ps.editor.actions@main`)

Inputs:

- `metadata-id` (required): Geonorge metadata UUID used to fetch psdata content.
- `ogc-feature-api`: Fully qualified URL to an OGC API - Features `/collections` endpoint. Optional; omit when you only want psdata or when using `xmi-model` instead.
- `feature-type-filter`: Optional list of feature type names to include (case-insensitive exact match). Provide multiple values to keep only selected feature types (applies to both OGC API and XMI).
- `scopes`: Optional YAML/JSON list (or file path) describing multiple scopes. When supplied, each scope generates its own `objektkatalog.md` under a scope-named folder, and the main `index.md` links to each scope catalogue while keeping the main data model diagram.
- `output-directory` (default `produktspesifikasjon`): Directory that will contain the generated artefacts.
- `product-slug`: Overrides the auto-generated folder name (derived from the psdata title).
- `template-path`: Path to a Handlebars-style template if you want to replace `data/template/ps.md.hbs`.
- `updated`: Explicit value for the `updated` field in the rendered Markdown front matter (propagated to the assemble step).
- `xmi-model`: Optional path or URL to a SOSI UML XMI feature catalogue. When supplied the OGC API input is ignored.
- `xmi-username` / `xmi-password` (default `sosi`/`sosi`): Credentials used to download the XMI catalogue.

Outputs:

- `spec-directory`: Absolute path to the directory containing all generated files.
- `psdata-path`: Path to the psdata JSON file.
- `feature-catalogue-json`: Path to the collected feature catalogue JSON cache.
- `feature-catalogue-markdown`: Path to the feature catalogue Markdown table (blank if no entries were found).
- `feature-catalogue-uml`: Path to the feature catalogue PlantUML diagram (blank if no entries were found).
- `xmi-feature-catalogue-json`: Path to the XMI feature catalogue JSON cache when generated.
- `xmi-feature-catalogue-markdown`: Path to the XMI feature catalogue Markdown table when generated.
- `xmi-feature-catalogue-uml`: Path to the XMI feature catalogue PlantUML diagram when generated.
- `spec-markdown`: Reserved path for the final product specification Markdown (always `<spec-directory>/index.md`). The file is created by the assemble action.

### Assemble specification (`arkitektum/ps.editor.actions/assemble@main`)

Inputs:

- `psdata-path` (required): Path to the psdata JSON file from the prepare step.
- `output-path` (required): Target path for the rendered Markdown specification.
- `template-path`: Optional override for the Handlebars-style template.
- `feature-catalogue-markdown`: Optional path to the feature catalogue Markdown table.
- `feature-catalogue-uml`: Optional path to the feature catalogue PlantUML source (embedded when no PNG is provided).
- `feature-catalogue-png`: Optional path to the rendered PlantUML PNG diagram. When missing or unavailable, the PlantUML source is embedded instead.
- `xmi-feature-catalogue-markdown`: Optional path to the XMI feature catalogue Markdown table.
- `xmi-feature-catalogue-uml`: Optional path to the XMI feature catalogue PlantUML source.
- `xmi-feature-catalogue-png`: Optional path to the rendered XMI PlantUML PNG diagram.
- `updated`: Optional override for the `updated` metadata field.

Empty headings are removed automatically when their sections end up blank after templating. If you want to keep the headings in place when running the script directly, pass `--keep-empty-headings`.

Outputs:

- `spec-markdown`: Path to the assembled product specification Markdown document (always `<spec-directory>/index.md`).

### Build Pages artefact (`arkitektum/ps.editor.actions/publish@main`)

This optional action wraps the static-site build and artefact upload steps before `actions/deploy-pages`. Example:

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: arkitektum/ps.editor.actions/publish@main
        with:
          checkout: false                 # already checked out
          upload-path: site

  deploy:
    runs-on: ubuntu-latest
    needs: build
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    permissions:
      pages: write
      id-token: write
    steps:
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v4
```

Inputs:

- `checkout` (default `false`): Set to `true` if the action should fetch the repository.
- `python-version` (default `3.11`): Passed to `actions/setup-python`.
- `requirements`: Additional requirements file to install; leave blank (default) to skip. The action installs `markdown` and `PyYAML` automatically.
- `extra-packages`: Additional pip packages to install.
- `upload-path` (default `site`): Directory uploaded via `actions/upload-pages-artifact`. The action invokes `build_github_pages.py` from this repository with `<source> --output <upload-path>`.
- `working-directory` (default `.`): Directory for installation and build commands.
- `pythonpath`: Exported as `PYTHONPATH` while running the build command.
- `source` (default `produktspesifikasjon`): Root directory that contains the generated Markdown specifications.
- `artifact-name` (default `github-pages`): Name of the uploaded artefact.

### Generate schemas (`arkitektum/ps.editor.actions/shapechange@main`)

Turns a feature catalogue into a GML application schema (XSD) and a JSON Schema by way of [ShapeChange](https://github.com/ShapeChange/ShapeChange). The action runs the two lightweight Python halves; ShapeChange itself is a 112 MB Java distribution and is executed by the calling workflow, exactly the way PlantUML is. See [Running ShapeChange](#running-shapechange) for the complete snippet.

Inputs:

- `mode` (default `generate`): `generate` writes the SCXML model and the ShapeChange configuration; `check` reads the log ShapeChange produced, fails the job on errors and uploads the artefact.
- `output-directory` (default `shapechange`): Directory holding the model, configuration, log and generated schemas.
- `feature-catalogue-json`: Path to a feature catalogue JSON file, e.g. the prepare action's `feature-catalogue-json` output. Works for every source the prepare action supports (OGC API, GeoPackage, XMI).
- `xmi-model`: Path or URL to a SOSI UML XMI feature catalogue, read directly instead of a JSON file.
- `xmi-username` / `xmi-password` (default `sosi`/`sosi`): Credentials used to download the XMI catalogue.
- `target-namespace` (required in `generate` mode): Target namespace of the application schema. None of the feature catalogue sources carry one, and ShapeChange selects application schemas by exactly this value.
- `xmlns-prefix` (default `app`): Namespace prefix of the application schema.
- `schema-version` (default `1.0`): Version of the application schema.
- `schema-name`: Name of the application schema package. Defaults to the input file stem.
- `xsd-document`: File name of the generated XML Schema document. Defaults to `<SchemaName>.xsd`.
- `targets` (default `xsd,json`): Which ShapeChange targets to enable.
- `xsd-encoding-rule` (default `sosi`): ShapeChange XML Schema encoding rule. `sosi` is Kartverket's SOSI profile, written into the generated configuration — see [The SOSI profile](#the-sosi-profile). Built-in alternatives are `iso19136_2007`, `gml33`, `iso19139_2007` and `ogcSweCommon2`.
- `json-schema-version` (default `2019-09`): One of `2020-12`, `2019-09`, `draft-07`, `OpenApi30`.
- `json-base-uri`: Base URI used when constructing `$id` values in the JSON Schema output.
- `json-encoding-rule` (default `sosiJson`): `sosiJson` is written into the generated configuration; the built-in alternatives are `defaultGeoJson` and `defaultPlainJson`.
- `represent-tagged-values` (default `SOSI_navn,SOSI_verdi,NVDB_ID`): Tagged values emitted as `sc:taggedValue` appinfo in the XSD. Needs an encoding rule with `rule-xsd-all-tagged-values`, which `sosi` has.
- `codelist-as-dictionary` (default `model`): `model`, `true` or `false`. Decides how code lists are encoded — see [Code lists](#code-lists).
- `entity-type-name` (default `@type`): Name of the entity type member in the JSON Schema output.
- `xml-schema-target-class` / `json-schema-target-class`: Override the ShapeChange target classes. The Java package names changed in ShapeChange 4.0.0; the defaults target 4.x.
- `bundled-includes` (default `false`): Reference the standard rules and map entries bundled with the ShapeChange distribution instead of the copies on `shapechange.net`. Requires running the jar from the distribution root.
- `upload-artifact` (default `true`) / `artifact-name` (default `shapechange-schemas`): Artefact upload in `check` mode.
- `python-version` (default `3.11`): Passed to `actions/setup-python`.

Outputs:

- `output-directory`: Directory holding the model, configuration, log and schemas.
- `scxml-model`: Path to the generated ShapeChange SCXML model.
- `shapechange-config`: Path to the generated ShapeChange configuration document — pass this to `java -jar ShapeChange.jar -c`.
- `shapechange-log`: Path to the XML log ShapeChange writes.
- `xsd-directory` / `json-schema-directory`: Directories that receive the generated schemas.

## Exporting PlantUML to PNG

PNG diagrams are recommended for readability, but the assemble action now falls back to embedding the PlantUML source when no PNG is supplied. Use the workflow snippet above—or any other conversion job—to generate PNGs when you want rendered graphics.

## Running ShapeChange

ShapeChange converts the data model into a GML application schema (XSD) and a JSON Schema. Like PlantUML, it is a heavyweight external tool, so the calling workflow runs it:

```yaml
      - id: schema-model
        uses: arkitektum/ps.editor.actions/shapechange@main
        with:
          feature-catalogue-json: ${{ steps.prepare.outputs.feature-catalogue-json }}
          target-namespace: https://skjema.geonorge.no/SOSI/produktspesifikasjon/DyrkbarJord/1.0
          xmlns-prefix: dyrkbarjord
          schema-name: DyrkbarJord
          json-base-uri: https://skjema.geonorge.no/SOSI/produktspesifikasjon/DyrkbarJord

      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: "21"

      - name: Cache ShapeChange
        id: shapechange-cache
        uses: actions/cache@v4
        with:
          path: ~/shapechange
          key: shapechange-4.0.0

      - name: Download ShapeChange
        if: steps.shapechange-cache.outputs.cache-hit != 'true'
        run: |
          set -euo pipefail
          mkdir -p ~/shapechange
          curl -sSL -o /tmp/shapechange.zip \
            https://github.com/ShapeChange/ShapeChange/releases/download/4.0.0/ShapeChange-4.0.0.zip
          unzip -q /tmp/shapechange.zip -d ~/shapechange

      - name: Run ShapeChange
        working-directory: ~/shapechange
        run: |
          set -euo pipefail
          java -Dfile.encoding=UTF-8 -jar ShapeChange-4.0.0.jar \
            -c "${{ steps.schema-model.outputs.shapechange-config }}"

      - id: schemas
        uses: arkitektum/ps.editor.actions/shapechange@main
        with:
          mode: check
          output-directory: ${{ steps.schema-model.outputs.output-directory }}
```

Things worth knowing about this pipeline:

- **ShapeChange is only distributed as a ZIP.** There is no fat jar and no Maven Central artefact, and `ShapeChange-4.0.0.jar` relies on a sibling `lib/` directory, so the whole archive has to be unpacked. It requires **Java 21**.
- **Always pass `-c`.** Without a configuration file ShapeChange opens a Swing dialog, which hangs a CI job.
- **`working-directory` is the distribution root** because ShapeChange resolves relative paths against the working directory. Everything this action writes into the configuration is an absolute path, so only the bundled `xi:include` references depend on it.
- **The `check` step is not optional.** ShapeChange returns a non-zero exit code only for a fatal abort; ordinary model and target errors are written to the log while the process still exits 0. `mode: check` parses that log, turns messages into GitHub annotations and fails the job when errors were reported.

### Feeding ShapeChange a model

ShapeChange cannot read the SOSI XMI files directly: its `XMI10` reader requires XMI **1.0** with a DOCTYPE, while the exports on `sosi.geonorge.no` are XMI **1.1**. The action therefore reads every source through this repository's own loaders and writes a ShapeChange **SCXML** model, which is the recommended interchange format. The same path serves OGC API, GeoPackage and XMI sources, so `feature-catalogue-json` is the input to reach for; `xmi-model` is a shortcut when there is no prepare step.

Because the JSON structure is flat, the model ShapeChange sees is reconstructed:

- top-level entries become `<<featureType>>` classes, nested attribute groups become `<<dataType>>` classes;
- value domains become `<<codeList>>` or `<<enumeration>>` according to the source model's stereotype, falling back on the presence of an external `codeList` URI for sources that carry no stereotypes;
- geometry is normalised to the ISO 19107 `GM_*` types, and other types to ISO 19103 names such as `CharacterString` and `Integer`;
- `SOSI_navn`, `SOSI_verdi` and `NVDB_ID` tagged values are carried through from XMI sources;
- `targetNamespace`, `xmlns`, `version`, `xsdDocument` and `jsonDocument` are synthesised from the action inputs, since no source carries them.

Constraints (OCL) and classes that are not feature types do not survive the trip through the JSON structure. GeoPackage sources are the thinnest input of all — no relationships, no packages, no code lists — so their schema is a flat rendering of the tables.

### The SOSI profile

Stock ShapeChange produces a generic ISO 19136 schema. The application schemas published on `skjema.geonorge.no` look different, and [kartverket/ShapeChange-Add-In](https://github.com/kartverket/ShapeChange-Add-In) shows why: Kartverket runs ShapeChange with a SOSI-specific encoding rule and an extra map-entry file. That add-in is a Windows-only Enterprise Architect wrapper, so none of it runs in CI — but the two configuration fragments are plain ShapeChange and are reused here.

`xsd-encoding-rule: sosi` (the default) writes Kartverket's rule into the generated configuration:

```xml
<EncodingRule name="sosi" extends="iso19136_2007">
  <rule name="rule-xsd-prop-length-size-pattern"/>
  <rule name="rule-xsd-all-tagged-values"/>
  <rule name="rule-xsd-all-notEncoded"/>
  <rule name="rule-xsd-pkg-schematron"/>
  <rule name="rule-xsd-prop-nillable"/>
  <rule name="rule-xsd-prop-targetCodeListURI"/>
</EncodingRule>
```

Every individual rule is stock ShapeChange — only the grouping is Kartverket's — so no patched ShapeChange is needed. Two of them do the visible work:

- `rule-xsd-all-tagged-values`, together with `represent-tagged-values`, emits `<sc:taggedValue tag="SOSI_navn">FLATE</sc:taggedValue>` appinfo, exactly as in the published Geonorge schemas;
- `rule-xsd-prop-targetCodeListURI` emits `<sc:targetCodeListURI>` carrying each code list's registry URI, so the link to `register.geonorge.no` survives whichever encoding the code list gets.

The profile is purely additive: running the same model through `iso19136_2007` and `sosi` yields structurally identical schemas, and the `sosi` output only gains the `sc:` import and the appinfo blocks.

Alongside it, `shapechange/data/StandardMapEntries_sosi.xml` maps the Norwegian SOSI type names — `Navn`, `Høyde`, `Dybde`, `Organisasjonsnummer`, `URI`, and the geometry types `Punkt`/`Kurve`/`Flate`/`Sverm`. It is vendored rather than fetched from `sosi.geonorge.no`, because the upstream copy is served over plain HTTP from a version-pinned path. One deliberate fix: upstream maps `Høyde` twice, to `double` and to `string`; only the `double` mapping is kept.

Set `xsd-encoding-rule: iso19136_2007` to opt out and get plain ShapeChange behaviour.

### Code lists

ShapeChange encodes a `<<codeList>>` in one of two shapes, chosen by its `asDictionary` tagged value:

| `asDictionary` | XSD |
|---|---|
| `true` | `<element name="kommunenummer" type="gml:CodeType"/>` |
| `false` | `<union memberTypes="app:KommunenummerEnumerationType app:KommunenummerOtherType"/>` with `pattern "other: \w{2,}"` |

The published Geonorge schemas use the union form throughout. `codelist-as-dictionary` decides which you get: `model` (default) keeps whatever the source model says, while `true`/`false` overrides it. The registry URI is preserved either way through `rule-xsd-prop-targetCodeListURI`, so the choice is about instance-document form, not about losing information.

An `<<enumeration>>` is different again — a closed `restriction` with no escape hatch. That is why the source stereotype is carried through rather than guessed.

### JSON Schema and non-ASCII class names

Both built-in JSON encoding rules include `rule-json-cls-name-as-anchor`, which encodes every class name as a JSON Schema `$anchor`. JSON Schema requires anchors to match `^[A-Za-z][-A-Za-z0-9.:_]*$`, so a Norwegian name such as `Målemetode` produces a document that fails strict metaschema validation.

ShapeChange encoding rules can only *add* rules, never remove one, so the fix is a rule that does not extend a built-in. That is what `json-encoding-rule: sosiJson` (the default) is: Kartverket's three-rule `defaultJson` plus what is needed for geometry, identity and documentation, and without the anchor rule. The result validates cleanly as Draft 2019-09 while keeping GeoJSON geometry (`"geometry": {"$ref": "https://geojson.org/schema/MultiPolygon.json"}`).

Set `json-encoding-rule: defaultGeoJson` for stock behaviour, at the cost of the `$anchor` problem returning.

### Namespace and version conventions

Geonorge date-stamps its application schemas, using the same stamp as both the last path segment of the namespace and the schema version:

```yaml
target-namespace: https://skjema.geonorge.no/SOSI/produktspesifikasjon/Dyrkbarjord/20250530
schema-version: "20250530"
xmlns-prefix: app
```

`app` is the prefix Geonorge uses, and is already the default.

## Template

The default template lives at `data/template/ps.md.hbs`. It expects the following placeholders to be populated by the generator:

- `incl_featuretypes_table`: Markdown table generated from the OGC API feature catalogue metadata.
- `incl_featuretypes_uml`: Feature catalogue diagram rendered as a PNG when available, otherwise the raw PlantUML source.
- `incl_featuretypes_xmi_table`: Markdown table generated from the XMI feature catalogue when provided.
- `incl_featuretypes_xmi_uml`: XMI feature catalogue diagram rendered as a PNG when available, otherwise the raw PlantUML source.

During the assemble step, every additional `*.md` file placed alongside the generated artefacts (the same directory that holds `psdata_<slug>.json`) is automatically injected into the template. A file named `innledning.md`, for example, becomes available through the placeholder `{{incl_innledning}}`. Files that already have a dedicated input—such as `index.md` or `<slug>_feature_catalogue.md`—are ignored to avoid conflicts.

You can provide a customised template via the `template-path` input to tailor the resulting Markdown documentation.

## Local development

Prepare artefacts locally:

```bash
python scripts/generate_product_spec.py <metadata-id> [<ogc-feature-api>] --output-dir produktspesifikasjon/test --skip-spec-markdown
```

If you want to filter the feature catalogue, pass `--feature-type-filter` multiple times or as a comma-separated list (applies to both OGC API and XMI sources):

```bash
python scripts/generate_product_spec.py <metadata-id> https://dirmin.no/kart/server/wfs3/collections \
  --feature-type-filter Uttak --feature-type-filter Konsesjon
```

To split the specification into multiple scopes, pass a YAML/JSON payload (or a path to a YAML/JSON file) via `scopes`:

```yaml
scopes:
  - name: datafangst
    url: https://sosi.geonorge.no/svn/SOSI/SOSI Del 3/Kommunal- og moderniseringsdepartementet/Arealplan/Arealplan 5.0/PlanleggingIgangsatt.xml
    generator: xmi
    description: Datamodellen brukes for å legge ved gml filer for planområdet som brukes i tjenesten for varsel om planoppstart.
  - name: innsynstjeneste
    url: https://plandata.ft-test.dibk.no/services/planleggingigangsatt/collections
    generator: ogc_feature_api
    description: Tjeneste for innsyn i planområder som er varslet for planlegging igangsatt.
```

Each scope writes its catalogue to `<spec-directory>/<scope-name>/objektkatalog.md`, and the main specification links to them.

If you have a SOSI UML XMI export instead of an OGC API, omit the second positional argument and pass `--xmi-model <path-or-url>` (optionally override the default `sosi`/`sosi` credentials with `--xmi-username` and `--xmi-password`). You can also omit the OGC API argument entirely to only fetch psdata (feature catalogue artefacts will still be created but remain empty). The generated files will use the `_xmi_feature_catalogue.*` suffix to keep them separate from OGC-based artefacts.

Once you have enriched the artefacts (e.g. rendered UML to PNG), assemble the final Markdown:

```bash
python scripts/assemble_product_spec.py produktspesifikasjon/test/<slug>/psdata_<slug>.json --output produktspesifikasjon/test/<slug>/index.md --feature-catalogue-markdown produktspesifikasjon/test/<slug>/<slug>_feature_catalogue.md --feature-catalogue-uml produktspesifikasjon/test/<slug>/<slug>_feature_catalogue.puml --feature-catalogue-png produktspesifikasjon/test/<slug>/<slug>_feature_catalogue.puml.png
```

Adjust paths to match your slug and any generated PNG files. The commands mirror the behaviour in GitHub Actions.

When you also generate an XMI-based catalogue, add the corresponding arguments:

```bash
--xmi-feature-catalogue-markdown produktspesifikasjon/test/<slug>/<slug>_xmi_feature_catalogue.md \
--xmi-feature-catalogue-uml produktspesifikasjon/test/<slug>/<slug>_xmi_feature_catalogue.puml
```

and optionally `--xmi-feature-catalogue-png <path>` if you render the XMI diagram to PNG.

To produce schemas locally, generate the ShapeChange input, run the jar from an unpacked ShapeChange distribution, then evaluate the log:

```bash
python scripts/run_shapechange.py --mode generate \
  --feature-catalogue produktspesifikasjon/test/<slug>/<slug>_feature_catalogue.json \
  --target-namespace https://example.no/minmodell/1.0 \
  --xmlns-prefix mm --schema-name MinModell \
  --output-dir build/shapechange

cd /path/to/ShapeChange-4.0.0
java -Dfile.encoding=UTF-8 -jar ShapeChange-4.0.0.jar -c /abs/path/build/shapechange/shapechange-config.xml
cd -

python scripts/run_shapechange.py --mode check --output-dir build/shapechange
```

The generated XSD lands in `build/shapechange/xsd/` and the JSON Schema in `build/shapechange/jsonschema/`.

## SOSI XMI feature catalogues

Support specification projects from Enterprise Architect XMI exports. You can convert those catalogues to the JSON structure expected by the rest of this repository via the `xmi.feature_catalog` module (or by passing `--xmi-model` to `scripts/generate_product_spec.py`):

```python
import json
from pathlib import Path
from xmi.feature_catalog import load_feature_types_from_xmi

feature_types = load_feature_types_from_xmi(
    "https://sosi.geonorge.no/svn/SOSI/SOSI%20Del%203/Statens%20kartverk/AdministrativeEnheter_FylkerOgKommuner-20240101.xml"
)

Path("feature_catalogue.json").write_text(json.dumps(feature_types, indent=2, ensure_ascii=False))
```

The helper understands both local files and remote URLs. When downloading from `sosi.geonorge.no` the default `sosi`/`sosi` credentials are supplied automatically, but you can override them via the `username` and `password` arguments if needed. When the XMI path is used, generated artefacts follow the `<slug>_xmi_feature_catalogue.*` naming scheme to make side-by-side comparisons with OGC sources easier.
