# Magazine Sorter

<p align="center">
  <img src="src/web/static/magazine-sorter.png" alt="Magazine Sorter" width="180">
</p>

> **AI-assisted development**
>
> Magazine Sorter was developed with substantial assistance from AI (OpenAI/ChatGPT). The code and design have been tested and reviewed by the author, who is responsible for the project and its maintenance.

Magazine Sorter is a conservative PDF/CBZ organizer designed for magazine libraries. It analyzes filenames first, uses OCR when configured for uncertain cases, presents ambiguous files for manual review, and only moves files after an explicit Apply step.

The project is designed to work well with **Komga** without trying to configure Komga itself.

## Core workflow

1. **Dry Run** scans the configured input folder.
2. Filename metadata and publication profiles are used first.
3. Files that cannot be classified safely are sent to **Review**.
4. OCR can provide additional publication/year/issue metadata for Review cases.
5. A proposed destination is shown before anything is changed.
6. **Apply Changes** moves only files that are READY.
7. Existing destinations are never overwritten.
8. **History** keeps an audit trail of runs and file operations.
9. Files moved by Apply can be safely **Undone** when their recorded integrity checks still match.

## Classification philosophy

Magazine Sorter is intentionally conservative.

- **AUTO**: enough information is available to propose a destination automatically.
- **REVIEW**: a human decision is required before the file can be moved.
- **IGNORE**: the file is intentionally excluded from organizing.
- **ERROR**: the file could not be processed and is excluded from Apply.
- **BLOCKED**: an otherwise valid move cannot be performed safely, for example because the destination already exists or multiple files target the same destination.

`REVIEW` is a safety feature, not a failure state. The application should prefer asking for a decision over guessing.

## Specials and standalone files

The project follows Komga-friendly folder semantics:

- One publication is one folder/Series.
- A special belonging to an existing publication stays in that publication folder.
- A special is treated as a book classification, not as a separate `Specials/` folder.
- Standalone publications use Komga's `_oneshots` convention rather than inventing a separate per-file folder structure.

For example:

```text
Hjemmet/
  Hjemmet - 2025 - Nr 01.pdf
  Hjemmet - 2025 - Christmas Magazine.pdf

_oneshots/
  Example Magazine.pdf
```

## Safety model

The application deliberately avoids destructive or ambiguous operations.

- No overwrite of an existing destination.
- Review files are never moved by Apply.
- A collision blocks the affected file(s), not unrelated READY files.
- Apply records source/destination paths and integrity information.
- Undo performs preflight checks before moving anything back.
- Undo verifies file size and SHA-256 where required by the recorded operation.
- Undo never force-overwrites an existing source file.
- If one Undo item becomes unsafe, other independently safe items can still be processed.

## History

History is an audit trail of what the application has done, not a second control panel for the sorting rules.

The intended lifecycle is:

```text
Original
  -> Dry Run REVIEW
  -> Manual decision
  -> Apply
  -> Library
  -> Undo
  -> Input
  -> Re-run
```

Apply and Undo operations retain their historical records so that the run remains understandable after later actions.

## Configuration

The application stores persistent settings/runtime state separately from source code. The following environment variables are supported by the current application:

- `MAGAZINE_SORTER_INPUT_FOLDER`
- `MAGAZINE_SORTER_OUTPUT_FOLDER`
- `MAGAZINE_SORTER_DATA_DIR`
- `MAGAZINE_SORTER_TESSDATA_DIR`
- `MAGAZINE_SORTER_OCR_IMAGE`
- `MAGAZINE_SORTER_OCR_BACKEND`

The web Settings page can manage paths unless an environment variable is being used as an override. In Docker/Unraid, `/config` is the persistent application-data directory and should be mapped to the Unraid `appdata` share.

### OCR

Danish OCR is supported through the configured OCR backend. The production Docker image bundles Tesseract and Danish language data, so OCR does not require a second OCR container or access to the Docker socket.

## Local development

Create a virtual environment and install the dependencies from `requirements.txt`.

The web application is exposed by the FastAPI application in:

```text
src.web.app:app
```

A typical development command is:

```text
uvicorn src.web.app:app --host 0.0.0.0 --port 8000
```

The exact OCR backend and folders should be configured for the environment rather than hard-coded into the source.

## Tests and checks

The repository currently contains focused Python test/check scripts under `src/` for metadata, publications, destinations, inventory and collision analysis. They are lightweight project checks rather than a pytest test suite.

Before committing changes, at minimum validate Python syntax for changed modules and JavaScript syntax for changed frontend files.

## Project structure

```text
src/
  classifier.py
  filename_parser.py
  metadata_parser.py
  magazine_profiles.py
  magazine_aliases.py
  publication_detector.py
  path_builder.py
  scanner.py
  ocr*.py
  dry_run.py
  review.py
  models.py
  web/
    app.py
    run_store.py
    settings_manager.py
    profile_manager.py
    auto_run.py
    static/
    templates/
  data/                 # local/runtime data; not committed
```

## Komga compatibility scope

Magazine Sorter is **Komga-friendly**, but it does not attempt to configure or control Komga.

The important compatibility assumption is that Komga creates a Series from directory structure. Therefore the organizer keeps all issues belonging to one publication at the same folder level and uses the `_oneshots` convention for standalone one-book series.

Komga metadata such as book numbers can be adjusted inside Komga after import when needed; Magazine Sorter focuses on safe file classification, naming and placement.

## Docker / Unraid

### Install from the GitHub template

Magazine Sorter can be installed on Unraid without using Community Applications. The project maintains its own Docker template in this repository.

In **Apps → Add Container**, add the GitHub repository as a custom template repository:

```text
https://github.com/Tunarb/magazine-sorter
```

Then select **Magazine Sorter** from the available templates. Unraid loads the XML template and pre-fills the container settings.

The template includes the project icon, WebUI action and the production image:

```text
ghcr.io/tunarb/magazine-sorter:latest
```

The template provides sensible defaults for the container-side settings:

- `PUID=99` — Unraid's conventional `nobody` user
- `PGID=100` — Unraid's conventional `users` group
- `TZ=Europe/Copenhagen` — change if your server uses another timezone
- OCR backend `local` — uses the Tesseract installation bundled in the image
- container port `8000`
- persistent `/config` mapping

The **Input** and **Library** host paths are intentionally left for the installer to choose, because these locations differ between Unraid installations.

Recommended mappings are:

```text
Unraid appdata share       -> /config
Magazine input folder      -> /input
Komga library              -> /library
```

`/config` contains persistent settings, publication profiles, resumable Dry Run state, auto-run state and History. Publication profile edits are stored in `/config/publication_profiles.json` rather than modifying the application image.

The input and library paths should be separate host folders. The application uses them as the source and destination of file moves.

The container is intentionally self-contained: it includes the web application, Python dependencies and local Tesseract with Danish language data. It does not need the Docker socket or a second OCR container.

The GitHub Actions workflow builds and publishes the GHCR image automatically from `main`.

No user-specific Windows paths or development test data belong in the production image.

## Contributing

Issues, bug reports, documentation improvements and pull requests are welcome. Please keep the project's conservative safety model intact: do not introduce silent overwrites, destructive automatic decisions, or behavior that turns uncertain classifications into guesses.

## License

Magazine Sorter is released under the **MIT License**. See `LICENSE` for the full license text.

## Design principles

- Conservative over clever.
- Review instead of guessing.
- Never overwrite.
- Keep an audit trail.
- Make file operations reversible when safely possible.
- Do not duplicate functionality that belongs in Komga.
- Keep deployment-specific paths out of application logic.
