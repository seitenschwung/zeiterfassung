# FastAPI blueprint

Starting point for Python APIs made with `dev --stack fastapi <name>` (claude-dev kit).
Same deploy contract as `seitenschwung/template` (port 8080, `/healthz`, `/version.txt`, hardened
image), shared pipeline `seitenschwung/template/.github/workflows/pipeline.yml@v1`, with lint and
tests in the Dockerfile `test` stage. The deploy setup (Coolify, secrets, `COOLIFY_APP_UUID`) is the
one in the README of `seitenschwung/template`.

Replace this README with one about the project.
