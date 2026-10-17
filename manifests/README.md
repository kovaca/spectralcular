# Manifests

Commit a plate-picker export here — ideally as `manifests/<book>/selection.json`
with the book's copy (title, introduction, cover text) beside it as `book.yaml`
— and run the **Build book from selection** workflow (Actions →
workflow_dispatch). Or build locally in a project folder of your own:

```bash
uv run spectralcular init ~/books/mine --book manifests/<book>/book.yaml
export SPECTRALCULAR_PROJECT_DIR=~/books/mine
uv run spectralcular import manifests/<book>/selection.json
uv run spectralcular candidates lock --force
uv run spectralcular build --skip-existing --keep-going
uv run spectralcular print
```

`examples/spectralcular-scenery/` is laid out the same way.
