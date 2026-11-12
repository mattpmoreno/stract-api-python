# Stract API Python SDK

Python client for [Stract API](https://stract-api.com).

Define a **struct**, give it **fields**, and run a **job** against text or documents to get back one record per document.

- API reference: [docs.stract-api.com](https://docs.stract-api.com)
- OpenAPI spec: [api.stract-api.com/spec/openapi.yaml](https://api.stract-api.com/spec/openapi.yaml)
- API quick start: [stract-api.com/guide/quick-start-api](https://stract-api.com/guide/quick-start-api/)

## Installation

```bash
pip install stract-api
```

Requires Python 3.11 or later. The import name is `stract_api`.

## Authentication

Requests are authenticated with an API key, sent as a bearer token. To create one, sign in to the Stract app and go to **Account → API Keys**. Additional keys require a Team or Enterprise subscription.

Create one `Configuration` with your API key, and one `ApiClient` from it:

```python
import os

import stract_api

config = stract_api.Configuration(access_token=os.environ["STRACT_API_KEY"])

with stract_api.ApiClient(config) as client:
    ...  # create API instances here, see below
```

Keep your API key out of source control. Load it from an environment variable or a secrets manager.

## API groups

Endpoints are grouped by area, and each group is its own class. Create an instance for each group you use, passing the same `client` to each:

```python
with stract_api.ApiClient(config) as client:
    structs = stract_api.StructApi(client)
    fields = stract_api.FieldApi(client)
    jobs = stract_api.JobApi(client)
    documents = stract_api.DocumentApi(client)
```

| Class | Methods |
|---|---|
| `StructApi` | `create_struct`, `get_structs`, `archive_struct` |
| `FieldApi` | `create_fields`, `get_fields`, `edit_field`, `delete_field` |
| `JobApi` | `upload_text_document_and_run_job`, `upload_document_process_and_run_job`, `create_job`, `get_job_by_id`, `get_job_output_json`, `get_job_output_csv`, `get_struct_jobs`, `get_user_jobs`, `cancel_job`, `archive_job` |
| `JobDocumentApi` | `get_job_documents`, `get_job_document_output` |
| `DocumentApi` | `upload_document`, `upload_document_no_catalog`, `upload_text_document`, `upload_text_document_no_catalog`, `get_user_documents`, `get_catalog_documents`, `get_documents_for_job`, `get_document_text`, `get_document_link`, `delete_document` |
| `CatalogApi` | `create_catalog`, `get_catalogs`, `delete_catalog` |
| `LlmModelApi` | `get_llm_models` |
| `CreditsApi` | `get_current_credits`, `get_current_org_credits` |
| `AccountCreditLedgerApi` | `get_account_credit_ledger_for_spending_user` |
| `UserApi` | `get_my_user`, `edit_my_user` |
| `AccountApi` | `get_account`, `edit_account`, `get_account_users`, `create_account_user` |
| `ApiKeyApi` | `get_api_keys` |

Methods and arguments use `snake_case`. Request bodies are Pydantic models, for example `stract_api.CreateStructRequest`. The examples below assume the instances created above, inside the `with` block.

## Quick start

### 1. Create a struct

A struct describes the data you want extracted.

```python
struct = structs.create_struct(
    stract_api.CreateStructRequest(name="Restaurant orders")
)
```

### 2. Add fields

Each field has a name, a description that guides the extraction, and a type.

```python
fields.create_fields(
    struct.id,
    [
        stract_api.CreateFieldRequest(
            name="hamburgers",
            description="Number of hamburgers the customer ordered",
            type=stract_api.FieldType.NUMBER,
        ),
    ],
)
```

Field types: `string`, `boolean`, `number`, `date`, `datetime`, `enum`. For `enum`, also pass `enum_values`.

### 3. Extract data from text

For synchronous jobs, always call the `_with_http_info` variant of the method and check the status before reading the result. See [Why use `_with_http_info` for synchronous jobs](#why-use-_with_http_info-for-synchronous-jobs).

```python
res = jobs.upload_text_document_and_run_job_with_http_info(
    struct.id,
    stract_api.SyncTextJobRequest(
        name="order-1042",
        text="Two hamburgers and a large fries",
        additional_instructions="",
        output_type="json",
    ),
)

if res.status_code == 202:
    # Still running: save the job ID and fetch the output later.
    job_id = res.headers["stract-job-id"]
else:
    for record in res.data:
        print(record.document_name, record.additional_properties)
        # order-1042 {'hamburgers': 2}
```

Each record is a `JobOutputRecord`. `document_name` is a normal attribute, and the extracted fields are in `additional_properties`. Use `record.to_dict()` for a single flat dict.

### 4. Extract data from a file

```python
from pathlib import Path

path = Path("invoice.pdf")

res = jobs.upload_document_process_and_run_job_with_http_info(
    struct.id,
    file=(path.name, path.read_bytes()),
    additional_instructions="Focus on extracting financial figures only",
    output_type="json",
)

if res.status_code == 202:
    job_id = res.headers["stract-job-id"]
else:
    records = res.data
```

Pass the file as a `(name, bytes)` tuple so the upload carries a file name. The API uses the extension to decide how to process it.

Supported file types include PDF, images (PNG, JPEG, WebP, HEIC, TIFF, AVIF, BMP), Office documents (DOC/DOCX, XLS/XLSX, PPT/PPTX), email (EML, MSG), RTF, and text formats (TXT, CSV, JSON, XML, HTML, Markdown, YAML and more). Audio is not supported on this endpoint.

## Synchronous vs. batch jobs

`upload_text_document_and_run_job` and `upload_document_process_and_run_job` upload, analyze, and return results in a single request. They work best for short documents (under about 20,000 characters).

| Limit | Value |
|---|---|
| File size | 100 MB |
| Raw text files (.txt, .csv, .json, …) | 10 MB |
| Text request body | 10 MB |
| Wait time before returning `202` | About 100 seconds |

### Why use `_with_http_info` for synchronous jobs

Every endpoint has two methods: a plain one (`upload_text_document_and_run_job`) that returns just the parsed result, and a `_with_http_info` one (`upload_text_document_and_run_job_with_http_info`) that returns an `ApiResponse` with `status_code`, `headers` and `data`.

If a synchronous job takes longer than the wait time, the API responds with `202` and the job keeps running. The response body is then an error message string rather than records, and the job ID is only in the `stract-job-id` header. The plain method returns that string as if it were the result, and you lose the job ID. The `_with_http_info` method lets you check `res.status_code` first and read the job ID from `res.headers`. Use it to poll the job and fetch its output, as shown below.

`200`, `202` and `422` responses from a synchronous job include `stract-document-id` and `stract-job-id` headers, so you can look up the document and job later.

### Batch jobs

For larger documents, or many documents at once, upload first and run an asynchronous job:

```python
import time
from pathlib import Path

# Upload a document
path = Path("contract.pdf")
doc = documents.upload_document_no_catalog(file=(path.name, path.read_bytes()))

# Start a job across one or more documents
job = jobs.create_job(
    struct.id,
    stract_api.CreateJobRequest(
        additional_instructions="",
        document_ids=[doc.id],
    ),
)

# Poll until the job finishes
current = job
while not current.is_complete:
    time.sleep(5)
    current = jobs.get_job_by_id(job.id)

# Fetch the results
if current.status == stract_api.JobStatus.COMPLETE:
    records = jobs.get_job_output_json(job.id)
```

To upload into a catalog, use `upload_document(catalog_id, file=...)` instead. A job can also run against every document in a catalog: pass `catalog_id` instead of `document_ids`.

## Using with asyncio

All methods are synchronous and block until the response arrives. In async code (FastAPI, bots, `asyncio` scripts), run them in a worker thread with `asyncio.to_thread` so the event loop isn't blocked. Pass the method and its arguments without calling it:

```python
import asyncio
import os

import stract_api


async def main():
    config = stract_api.Configuration(access_token=os.environ["STRACT_API_KEY"])

    with stract_api.ApiClient(config) as client:
        jobs = stract_api.JobApi(client)

        # One call, awaited without blocking the event loop
        res = await asyncio.to_thread(
            jobs.upload_text_document_and_run_job_with_http_info,
            struct_id,
            request,
        )

        # Several calls in parallel
        results = await asyncio.gather(
            *(
                asyncio.to_thread(
                    jobs.upload_text_document_and_run_job_with_http_info,
                    struct_id,
                    req,
                )
                for req in requests
            )
        )


asyncio.run(main())
```

Notes:

- Sharing one `ApiClient` across threads should work, but is not guaranteed. For a strict 
  guarantee, create one client per thread or task.

## Choosing a model

List the available LLM models with `get_llm_models()`, then pass a model's ID as `llm_model_id` on any job request. If you omit it, the job uses the default model. Alternatively, pass `llm_model_bucket` (`"l"`, `"m"` or `"h"`) to pick the cost-optimal model at a low, medium or high cost tier. It takes precedence over `llm_model_id`.

```python
models = stract_api.LlmModelApi(client).get_llm_models()
```

## Error handling

Non-2xx responses raise `stract_api.ApiException`. The status, reason, response body and headers are on the exception.

```python
from stract_api import ApiException

try:
    res = jobs.upload_text_document_and_run_job_with_http_info(...)
except ApiException as err:
    print(err.status, err.body)
    raise
```

Common statuses:

| Status | Meaning |
|---|---|
| `400` | Invalid request, unsupported file type, or file too large |
| `401` | Missing or invalid API key, or not permitted |
| `402` | Not enough credits |
| `422` | The job finished without producing output |

Invalid arguments, such as a `SyncTextJobRequest` with a missing field, raise a Pydantic `ValidationError` before any request is sent.

## Credits

Processing documents uses credits. Check your balance with `CreditsApi`, and see the history with `AccountCreditLedgerApi`:

```python
credits_api = stract_api.CreditsApi(client)

credits = credits_api.get_current_credits()
org_credits = credits_api.get_current_org_credits()
ledger = stract_api.AccountCreditLedgerApi(client).get_account_credit_ledger_for_spending_user()
```

## Development

This SDK is generated from the OpenAPI spec with [OpenAPI Generator](https://openapi-generator.tech) (`python` generator). Code in `output/` is generated. Don't edit it by hand, because the next generation will overwrite it. To protect a hand-edited file, list it in `output/.openapi-generator-ignore`.

Requirements: [uv](https://docs.astral.sh/uv/), Python 3.13 for the generator script, and Java 11+.

```bash
uv sync
uv run main.py   # download the live spec and regenerate output/
```

`main.py` saves the spec to `input/openapi.yaml` and runs the generator with the options in `openapi-generator-config.yaml`.

### Tests

The tests in `tests/test_sdk.py` follow the flows in this README: authentication, structs and fields, synchronous text and file jobs, a batch job, error handling, and the `asyncio.to_thread` pattern. They import the generated SDK straight from `output/`, so run `uv run main.py` first if `output/` doesn't exist.

Most tests call the live API and use a small amount of credits. They create a uniquely named struct and archive it when the run ends, and the batch test deletes the document it uploads.

1. Create a `.env` file in the repo root (it is git-ignored):

   ```
   STRACT_API_KEY=your-api-key
   ```

2. Run the tests:

   ```bash
   uv sync
   uv run pytest
   ```

Without `STRACT_API_KEY`, the live tests are skipped and only the offline tests run. The batch test can take a few minutes, because it polls until the job completes. To skip it, run `uv run pytest -k "not batch"`. A synchronous test is also skipped if the API returns `202` because the job outlasted the sync wait time.

## Publishing

The package is published to [PyPI](https://pypi.org/project/stract-api/) by the GitHub Actions workflow in `.github/workflows/publish.yml`. The workflow regenerates the SDK from the production spec, builds `output/`, and uploads it using PyPI [trusted publishing](https://docs.pypi.org/trusted-publishers/), so no API token is stored anywhere.

### Releasing a new version

PyPI never accepts the same version twice, so every release needs a new version number.

1. Create a new branch for the release.

2. Set the new version in `openapi-generator-config.yaml`:

   ```yaml
   packageVersion: 0.2.0
   ```

   Bump the patch version for fixes, the minor version for new endpoints or fields, and the major version for breaking changes. The SDK version is independent of the API spec version.

3. Regenerate locally and run the tests:

   ```bash
   uv run main.py
   uv run pytest
   ```

   Check that `output/pyproject.toml` shows the new version and `license = { text = "MIT" }`, and that `output/LICENSE` exists.

4. Commit the change and merge the branch into `main`.

5. Tag the release commit on `main` and push the tag. The tag name should match `packageVersion`, with a `v` in front:

   ```bash
   git checkout main
   git pull
   git tag v0.2.0
   git push origin v0.2.0
   ```

If a release is broken, don't try to re-upload it. Fix the problem, bump the version, and 
release again. You can also yank the bad version on pypi.org so that installers skip it.0

## Support

Questions, or need a file type that isn't supported? Contact [matt@stract-api.com](mailto:matt@stract-api.com).

## License

[MIT](LICENSE)
