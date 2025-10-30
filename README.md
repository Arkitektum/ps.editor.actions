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
- `ogc-feature-api` (required): Fully qualified URL to an OGC API - Features `/collections` endpoint.
- `output-directory` (default `produktspesifikasjon`): Directory that will contain the generated artefacts.
- `product-slug`: Overrides the auto-generated folder name (derived from the psdata title).
- `template-path`: Path to a Handlebars-style template if you want to replace `data/template/ps.md.hbs`.
- `updated`: Explicit value for the `updated` field in the rendered Markdown front matter (propagated to the assemble step).

Outputs:

- `spec-directory`: Absolute path to the directory containing all generated files.
- `psdata-path`: Path to the psdata JSON file.
- `feature-catalogue-json`: Path to the collected feature catalogue JSON cache.
- `feature-catalogue-markdown`: Path to the feature catalogue Markdown table (blank if no entries were found).
- `feature-catalogue-uml`: Path to the feature catalogue PlantUML diagram (blank if no entries were found).
- `spec-markdown`: Reserved path for the final product specification Markdown (always `<spec-directory>/index.md`). The file is created by the assemble action.

### Assemble specification (`arkitektum/ps.editor.actions/assemble@main`)

Inputs:

- `psdata-path` (required): Path to the psdata JSON file from the prepare step.
- `output-path` (required): Target path for the rendered Markdown specification.
- `template-path`: Optional override for the Handlebars-style template.
- `feature-catalogue-markdown`: Optional path to the feature catalogue Markdown table.
- `feature-catalogue-uml`: Optional path to the feature catalogue PlantUML source (handy for downstream tooling, not embedded).
- `feature-catalogue-png` (required): Path to the rendered PlantUML PNG diagram that will be embedded in the specification.
- `updated`: Optional override for the `updated` metadata field.

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
- `upload-path` (default `site`): Directory uploaded via `actions/upload-pages-artifact`. The action invokes `build_github_pages.py` from this repository with `--output <upload-path>`.
- `working-directory` (default `.`): Directory for installation and build commands.
- `pythonpath`: Exported as `PYTHONPATH` while running the build command.
- `artifact-name` (default `github-pages`): Name of the uploaded artefact.

## Exporting PlantUML to PNG

The assemble action requires a PNG diagram; this repository does not render it directly to avoid bundling Java/Graphviz. Use the workflow snippet above—or any other conversion job—to transform the PlantUML source before invoking the assemble action.

## Template

The default template lives at `data/template/ps.md.hbs`. It expects the following placeholders to be populated by the generator:

- `incl_featuretypes_table`: Markdown table generated from the feature catalogue metadata.
- `incl_featuretypes_uml`: PNG rendering of the feature catalogue diagram.

You can provide a customised template via the `template-path` input to tailor the resulting Markdown documentation.

## Local development

Prepare artefacts locally:

```bash
python scripts/generate_product_spec.py <metadata-id> <ogc-feature-api> --output-dir produktspesifikasjon/test --skip-spec-markdown
```

Once you have enriched the artefacts (e.g. rendered UML to PNG), assemble the final Markdown:

```bash
python scripts/assemble_product_spec.py produktspesifikasjon/test/<slug>/psdata_<slug>.json --output produktspesifikasjon/test/<slug>/index.md --feature-catalogue-markdown produktspesifikasjon/test/<slug>/<slug>_feature_catalogue.md --feature-catalogue-uml produktspesifikasjon/test/<slug>/<slug>_feature_catalogue.puml --feature-catalogue-png produktspesifikasjon/test/<slug>/<slug>_feature_catalogue.puml.png
```

Adjust paths to match your slug and any generated PNG files. The commands mirror the behaviour in GitHub Actions.
