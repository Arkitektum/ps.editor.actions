# ps.editor.actions - Generate Product Specifications

This repository provides a reusable GitHub Action that fetches metadata from Geonorge and feature catalogue information from an OGC API - Features endpoint to assemble a complete product specification package. The action downloads psdata JSON, builds feature catalogue caches (JSON, Markdown, PlantUML) and renders a Markdown specification using the bundled Handlebars-style template or a custom template you supply.

## Usage

```yaml
jobs:
  produktspesifikasjon:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - id: spec
        uses: arkitektum/ps.editor.actions@main
        with:
          metadata-id: 12345678-abcd-1234-abcd-1234567890ab
          ogc-feature-api: https://example.com/collections
          output-directory: produktspesifikasjon
          product-slug: mitt-produkt
          updated: 2025-01-01
      - name: Inspect generated files
        run: |
          echo "Markdown: ${{ steps.spec.outputs.spec-markdown }}"
          echo "psdata:  ${{ steps.spec.outputs.psdata-path }}"
```

Only `metadata-id` and `ogc-feature-api` are required. All other inputs are optional.

## Inputs

- `metadata-id` (required): Geonorge metadata UUID used to fetch psdata content.
- `ogc-feature-api` (required): Fully qualified URL to an OGC API - Features `/collections` endpoint.
- `output-directory` (default `produktspesifikasjon`): Directory that will contain the generated artefacts.
- `product-slug`: Overrides the auto-generated folder name (derived from the psdata title).
- `template-path`: Path to a Handlebars-style template if you want to replace `data/template/ps.md.hbs`.
- `updated`: Explicit value for the `updated` field in the rendered Markdown front matter.

## Outputs

- `spec-directory`: Absolute path to the directory containing all generated files.
- `psdata-path`: Path to the psdata JSON file.
- `feature-catalogue-json`: Path to the collected feature catalogue JSON cache.
- `feature-catalogue-markdown`: Path to the feature catalogue Markdown table (blank if no entries were found).
- `feature-catalogue-uml`: Path to the feature catalogue PlantUML diagram (blank if no entries were found).
- `spec-markdown`: Path to the rendered product specification Markdown document.

## Template

The default template lives at `data/template/ps.md.hbs`. It expects the following placeholders to be populated by the generator:

- `incl_psdata_json`: JSON representation of the psdata payload, wrapped in a fenced code block.
- `incl_featuretypes_table`: Markdown table generated from the feature catalogue metadata.
- `incl_featuretypes_uml`: PlantUML diagram rendered as a fenced code block.

You can provide a customised template via the `template-path` input to tailor the resulting Markdown documentation.

## Local development

Run the generator script directly to verify changes before publishing:

```bash
python scripts/generate_product_spec.py <metadata-id> <ogc-feature-api> --output-dir produktspesifikasjon/test
```

The command writes all artefacts to the selected output directory and prints the resolved file paths, mirroring the behaviour in GitHub Actions.
