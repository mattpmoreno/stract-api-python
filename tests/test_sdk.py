"""Live tests for the generated SDK, following the flows in README.md.

These call the real Stract API and spend a small amount of credits.
STRACT_API_KEY is read from the .env file in the repo root.
"""
import asyncio
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from dotenv import load_dotenv
from pydantic import ValidationError

import stract_api
from stract_api import ApiException

load_dotenv(Path(__file__).parent.parent / ".env")

API_KEY = os.environ.get("STRACT_API_KEY")
# Tests run against a deployment you choose (e.g. staging), never the SDK's
# built-in production host. Both variables live in .env.
BASE_URL = os.environ.get("STRACT_API_BASE_URL")

requires_key = pytest.mark.skipif(
    not (API_KEY and BASE_URL),
    reason="STRACT_API_KEY and STRACT_API_BASE_URL must both be set in .env",
)

BATCH_TIMEOUT_SECONDS = 120
REQUEST_TIMEOUT_SECONDS = 30
POLL_INTERVAL_SECONDS = 5


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

@pytest.fixture(scope="session")
def client():
    config = stract_api.Configuration(host=BASE_URL, access_token=API_KEY)
    with stract_api.ApiClient(config) as api_client:
        yield api_client


@pytest.fixture(scope="session")
def struct(client):
    """A struct with one number field, archived when the test session ends."""
    structs = stract_api.StructApi(client)
    created = structs.create_struct(
        stract_api.CreateStructRequest(name=f"sdk-test-{uuid.uuid4().hex[:8]}")
    )
    stract_api.FieldApi(client).create_fields(
        created.id,
        [
            stract_api.CreateFieldRequest(
                name="hamburgers",
                description="Number of hamburgers the customer ordered",
                type=stract_api.FieldType.NUMBER,
            )
        ],
    )
    yield created
    structs.archive_struct(created.id)


def sync_text_request(name="order-1042", text="Two hamburgers and a large fries"):
    return stract_api.SyncTextJobRequest(
        name=name,
        text=text,
        additional_instructions="",
        output_type="json",
    )


# --------------------------------------------------------------------------
# Offline tests (no API key or network needed)
# --------------------------------------------------------------------------

def test_invalid_output_type_is_rejected_before_request():
    with pytest.raises(ValidationError):
        stract_api.SyncTextJobRequest(
            name="order-1042",
            text="Two hamburgers",
            additional_instructions="",
            output_type="xml",
        )


def test_field_types_match_readme():
    values = {t.value for t in stract_api.FieldType}
    assert values == {"string", "boolean", "number", "date", "datetime", "enum"}


# --------------------------------------------------------------------------
# Authentication and errors
# --------------------------------------------------------------------------

@requires_key
def test_invalid_api_key_raises_api_exception():
    config = stract_api.Configuration(host=BASE_URL, access_token="not-a-real-key")
    with stract_api.ApiClient(config) as bad_client:
        with pytest.raises(ApiException) as exc:
            stract_api.CreditsApi(bad_client).get_current_credits()
    assert exc.value.status == 401


# --------------------------------------------------------------------------
# Read-only endpoints
# --------------------------------------------------------------------------

@requires_key
def test_get_llm_models(client):
    models = stract_api.LlmModelApi(client).get_llm_models()
    assert len(models) > 0


@requires_key
def test_credits(client):
    credits_api = stract_api.CreditsApi(client)
    assert credits_api.get_current_credits() is not None
    assert credits_api.get_current_org_credits() is not None


@requires_key
def test_credit_ledger(client):
    ledger = stract_api.AccountCreditLedgerApi(client).get_account_credit_ledger_for_spending_user()
    assert isinstance(ledger, list)


# --------------------------------------------------------------------------
# Structs and fields
# --------------------------------------------------------------------------

@requires_key
def test_struct_appears_in_list(client, struct):
    all_structs = stract_api.StructApi(client).get_structs()
    assert struct.id in {s.id for s in all_structs}


@requires_key
def test_struct_has_created_field(client, struct):
    fields = stract_api.FieldApi(client).get_fields(struct.id)
    assert [f.name for f in fields] == ["hamburgers"]


# --------------------------------------------------------------------------
# Synchronous jobs
# --------------------------------------------------------------------------

@requires_key
def test_sync_text_job_with_http_info(client, struct):
    jobs = stract_api.JobApi(client)
    res = jobs.upload_text_document_and_run_job_with_http_info(struct.id, sync_text_request())

    assert res.status_code in (200, 202)
    # Both statuses carry the job ID header, per the README.
    assert res.headers["stract-job-id"]

    if res.status_code == 202:
        pytest.skip("Job took longer than the sync wait time; returned 202")

    assert len(res.data) == 1
    record = res.data[0]
    assert record.document_name == "order-1042"
    assert "hamburgers" in record.additional_properties


@requires_key
def test_sync_file_job_with_http_info(client, struct):
    jobs = stract_api.JobApi(client)
    res = jobs.upload_document_process_and_run_job_with_http_info(
        struct.id,
        file=("order-1043.txt", b"Three hamburgers and a soda"),
        additional_instructions="",
        output_type="json",
    )

    assert res.status_code in (200, 202)
    assert res.headers["stract-job-id"]

    if res.status_code == 202:
        pytest.skip("Job took longer than the sync wait time; returned 202")

    assert len(res.data) == 1
    assert "hamburgers" in res.data[0].additional_properties


# --------------------------------------------------------------------------
# Batch job
# --------------------------------------------------------------------------

@requires_key
def test_batch_job_flow(client, struct):
    documents = stract_api.DocumentApi(client)
    jobs = stract_api.JobApi(client)

    doc = documents.upload_document_no_catalog(
        file=("order-1044.txt", b"One hamburger and two milkshakes"),
        _request_timeout=REQUEST_TIMEOUT_SECONDS,
    )
    try:
        job = jobs.create_job(
            struct.id,
            stract_api.CreateJobRequest(additional_instructions="", document_ids=[doc.id]),
            _request_timeout=REQUEST_TIMEOUT_SECONDS,
        )
        print(f"\nbatch job {job.id}: created with status={job.status.value}, is_complete={job.is_complete}")

        terminal = {
            stract_api.JobStatus.COMPLETE,
            stract_api.JobStatus.ERROR,
            stract_api.JobStatus.CANCELLED,
        }
        deadline = time.monotonic() + BATCH_TIMEOUT_SECONDS
        current = job
        while not current.is_complete and current.status not in terminal:
            assert time.monotonic() < deadline, (
                f"Batch job did not finish in {BATCH_TIMEOUT_SECONDS}s; "
                f"last status={current.status.value}, notes={current.notes!r}"
            )
            time.sleep(POLL_INTERVAL_SECONDS)
            current = jobs.get_job_by_id(job.id, _request_timeout=REQUEST_TIMEOUT_SECONDS)
            print(f"batch job {job.id}: status={current.status.value}, is_complete={current.is_complete}")

        assert current.status == stract_api.JobStatus.COMPLETE, f"Job ended as {current.status.value}: {current.notes!r}"

        records = jobs.get_job_output_json(job.id, _request_timeout=REQUEST_TIMEOUT_SECONDS)
        assert len(records) == 1
        assert "hamburgers" in records[0].additional_properties
    finally:
        documents.delete_document(doc.id, _request_timeout=REQUEST_TIMEOUT_SECONDS)


# --------------------------------------------------------------------------
# asyncio usage from the README
# --------------------------------------------------------------------------

@requires_key
def test_to_thread_pattern(client):
    models_api = stract_api.LlmModelApi(client)

    async def run():
        return await asyncio.gather(
            asyncio.to_thread(models_api.get_llm_models),
            asyncio.to_thread(models_api.get_llm_models),
        )

    first, second = asyncio.run(run())
    assert len(first) > 0
    assert len(second) == len(first)


# ==========================================================================
# Endpoint coverage
#
# Every sequence below leaves the account as it found it: whatever a test
# creates it deletes or archives, and whatever it edits it restores.
# ==========================================================================

TERMINAL_STATUSES = {
    stract_api.JobStatus.COMPLETE,
    stract_api.JobStatus.ERROR,
    stract_api.JobStatus.CANCELLED,
}


@contextmanager
def skip_if_unauthorized(reason):
    """Admin-only endpoints return 401 for non-admin keys; skip rather than fail."""
    try:
        yield
    except ApiException as exc:
        if exc.status == 401:
            pytest.skip(reason)
        raise


def unique(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def wait_for_job(jobs, job):
    """Poll a job until it reaches a terminal status and return it."""
    deadline = time.monotonic() + BATCH_TIMEOUT_SECONDS
    current = job
    while not current.is_complete and current.status not in TERMINAL_STATUSES:
        assert time.monotonic() < deadline, (
            f"Job {job.id} did not finish in {BATCH_TIMEOUT_SECONDS}s; "
            f"last status={current.status.value}, notes={current.notes!r}"
        )
        time.sleep(POLL_INTERVAL_SECONDS)
        current = jobs.get_job_by_id(job.id, _request_timeout=REQUEST_TIMEOUT_SECONDS)
    return current


@pytest.fixture(scope="session")
def finished_job(client, struct):
    """A completed batch job over one text document.

    Shared by the job read-endpoint tests so only one job is paid for.
    Cleanup archives the job and deletes the document.
    """
    documents = stract_api.DocumentApi(client)
    jobs = stract_api.JobApi(client)

    doc = documents.upload_text_document_no_catalog(
        stract_api.UploadTextDocumentRequest(
            name=unique("finished-job-doc"),
            text="Four hamburgers and a chocolate shake",
        ),
        _request_timeout=REQUEST_TIMEOUT_SECONDS,
    )
    job = None
    try:
        job = jobs.create_job(
            struct.id,
            stract_api.CreateJobRequest(additional_instructions="", document_ids=[doc.id]),
            _request_timeout=REQUEST_TIMEOUT_SECONDS,
        )
        done = wait_for_job(jobs, job)
        assert done.status == stract_api.JobStatus.COMPLETE, (
            f"Job ended as {done.status.value}: {done.notes!r}"
        )
        yield done, doc
    finally:
        if job is not None:
            jobs.archive_job(job.id)
        documents.delete_document(doc.id)


# --------------------------------------------------------------------------
# User and account
# --------------------------------------------------------------------------

@requires_key
def test_get_my_user(client):
    user = stract_api.UserApi(client).get_my_user()
    assert user.email
    assert user.is_active


@requires_key
def test_edit_my_user_and_restore(client):
    users = stract_api.UserApi(client)
    original = users.get_my_user()
    temp_name = unique("sdk-test")
    try:
        edited = users.edit_my_user(stract_api.EditMyUserRequest(first_name=temp_name))
        assert edited.first_name == temp_name
        assert users.get_my_user().first_name == temp_name
    finally:
        restored = users.edit_my_user(
            stract_api.EditMyUserRequest(first_name=original.first_name)
        )
    assert restored.first_name == original.first_name
    assert restored.last_name == original.last_name


@requires_key
def test_get_account(client):
    account = stract_api.AccountApi(client).get_account()
    assert account.name
    assert account.account_user_role in set(stract_api.AccountUserRole)


@requires_key
def test_edit_account_and_restore(client):
    accounts = stract_api.AccountApi(client)
    original = accounts.get_account()
    temp_name = unique("sdk-test-account")
    with skip_if_unauthorized("Editing the account requires an admin key"):
        edited = accounts.edit_account(stract_api.EditAccountRequest(name=temp_name))
        try:
            assert edited.name == temp_name
            assert accounts.get_account().name == temp_name
        finally:
            restored = accounts.edit_account(stract_api.EditAccountRequest(name=original.name))
        assert restored.name == original.name
        assert restored.id == original.id


@requires_key
def test_get_account_users(client):
    with skip_if_unauthorized("Listing account users requires an admin key"):
        account_users = stract_api.AccountApi(client).get_account_users()
    me = stract_api.UserApi(client).get_my_user()
    assert me.id in {u.suser_id for u in account_users}


@requires_key
def test_get_api_keys(client):
    with skip_if_unauthorized("Listing API keys requires an admin key"):
        keys = stract_api.ApiKeyApi(client).get_api_keys()
    assert len(keys) > 0
    assert API_KEY.endswith(keys[0].key_last_four) or any(
        API_KEY.endswith(k.key_last_four) for k in keys
    )


# --------------------------------------------------------------------------
# Structs: create -> list -> archive
# --------------------------------------------------------------------------

@requires_key
def test_struct_create_list_archive(client):
    structs = stract_api.StructApi(client)
    created = structs.create_struct(stract_api.CreateStructRequest(name=unique("sdk-struct")))
    try:
        assert created.name.startswith("sdk-struct-")
        assert created.id in {s.id for s in structs.get_structs()}
        # A fresh struct has no fields and no jobs.
        assert stract_api.FieldApi(client).get_fields(created.id) == []
        assert stract_api.JobApi(client).get_struct_jobs(created.id) == []
    finally:
        archived_id = structs.archive_struct(created.id)
    assert archived_id == created.id


# --------------------------------------------------------------------------
# Fields: create -> edit -> delete
# --------------------------------------------------------------------------

@requires_key
def test_field_create_edit_delete(client, struct):
    fields_api = stract_api.FieldApi(client)
    baseline = {f.id for f in fields_api.get_fields(struct.id)}

    created = fields_api.create_fields(
        struct.id,
        [
            stract_api.CreateFieldRequest(
                name="customer_name",
                description="Name of the customer",
                type=stract_api.FieldType.STRING,
            ),
            stract_api.CreateFieldRequest(
                name="size",
                description="Size of the drink",
                type=stract_api.FieldType.ENUM,
                enum_values=["small", "large"],
            ),
        ],
    )
    created_ids = [f.id for f in created]
    try:
        assert {f.name for f in created} == {"customer_name", "size"}
        by_name = {f.name: f for f in created}
        assert by_name["size"].enum_values == ["small", "large"]

        # Edit a plain property.
        renamed = fields_api.edit_field(
            by_name["customer_name"].id,
            stract_api.EditFieldRequest(
                name="client_name", description="Name of the client"
            ),
        )
        assert renamed.name == "client_name"
        assert renamed.description == "Name of the client"
        assert renamed.type == stract_api.FieldType.STRING

        # Edit enum values.
        resized = fields_api.edit_field(
            by_name["size"].id,
            stract_api.EditFieldRequest(enum_values=["small", "medium", "large"]),
        )
        assert resized.enum_values == ["small", "medium", "large"]

        listed = {f.id: f for f in fields_api.get_fields(struct.id)}
        assert set(created_ids) <= set(listed)
        assert listed[by_name["customer_name"].id].name == "client_name"
    finally:
        for field_id in created_ids:
            deleted = fields_api.delete_field(field_id)
            assert deleted.field_id == field_id

    assert {f.id for f in fields_api.get_fields(struct.id)} == baseline


# --------------------------------------------------------------------------
# Catalogs and catalog documents: create -> upload -> read -> delete
# --------------------------------------------------------------------------

@requires_key
def test_catalog_documents_lifecycle(client):
    catalogs = stract_api.CatalogApi(client)
    documents = stract_api.DocumentApi(client)

    catalog = catalogs.create_catalog(stract_api.CreateCatalogRequest(name=unique("sdk-catalog")))
    try:
        assert catalog.id in {c.id for c in catalogs.get_catalogs()}
        assert documents.get_catalog_documents(catalog.id) == []

        text_doc = documents.upload_text_document(
            catalog.id,
            stract_api.UploadTextDocumentRequest(
                name="catalog-note",
                text="Two hamburgers and a large fries",
            ),
            _request_timeout=REQUEST_TIMEOUT_SECONDS,
        )
        file_doc = documents.upload_document(
            catalog.id,
            file=("catalog-file.txt", b"Three hamburgers and a soda"),
            _request_timeout=REQUEST_TIMEOUT_SECONDS,
        )
        assert text_doc.catalog_id == catalog.id
        assert file_doc.catalog_id == catalog.id
        assert file_doc.extension.lstrip(".") == "txt"

        in_catalog = {d.id for d in documents.get_catalog_documents(catalog.id)}
        assert in_catalog == {text_doc.id, file_doc.id}
        assert {text_doc.id, file_doc.id} <= {d.id for d in documents.get_user_documents()}

        # Text is available immediately for text uploads.
        text = documents.get_document_text(catalog.id, text_doc.id)
        assert text.text is not None and "hamburgers" in text.text

        # File uploads are processed asynchronously, so the text may be null for now.
        file_text = documents.get_document_text(catalog.id, file_doc.id)
        assert file_text.text is None or "hamburgers" in file_text.text

        # The link points at a downloadable file, so it only exists for file uploads;
        # text documents live in the database and have nothing to link to.
        link = documents.get_document_link(file_doc.id)
        assert link.link.startswith("http")

        # Deleting a single document leaves the rest of the catalog alone.
        removed = documents.delete_document(file_doc.id)
        assert removed.document_id == file_doc.id
        remaining = {d.id for d in documents.get_catalog_documents(catalog.id)}
        assert remaining == {text_doc.id}
    finally:
        # Deleting the catalog also deletes any documents still in it.
        catalogs.delete_catalog(catalog.id)

    assert catalog.id not in {c.id for c in catalogs.get_catalogs()}
    assert text_doc.id not in {d.id for d in documents.get_user_documents()}


# --------------------------------------------------------------------------
# Documents without a catalog: upload -> read -> delete
# --------------------------------------------------------------------------

@requires_key
def test_text_document_no_catalog_lifecycle(client):
    documents = stract_api.DocumentApi(client)
    doc = documents.upload_text_document_no_catalog(
        stract_api.UploadTextDocumentRequest(
            name=unique("sdk-text-doc"),
            text="One hamburger, no onions",
        ),
        _request_timeout=REQUEST_TIMEOUT_SECONDS,
    )
    try:
        assert doc.id in {d.id for d in documents.get_user_documents()}
        text = documents.get_document_text(doc.catalog_id, doc.id)
        assert text.text is not None and "hamburger" in text.text
    finally:
        removed = documents.delete_document(doc.id)
    assert removed.document_id == doc.id
    assert doc.id not in {d.id for d in documents.get_user_documents()}


@requires_key
def test_file_document_no_catalog_lifecycle(client):
    documents = stract_api.DocumentApi(client)
    doc = documents.upload_document_no_catalog(
        file=(f"{unique('sdk-file-doc')}.txt", b"Five hamburgers"),
        _request_timeout=REQUEST_TIMEOUT_SECONDS,
    )
    try:
        assert doc.extension.lstrip(".") == "txt"
        assert doc.id in {d.id for d in documents.get_user_documents()}
        assert documents.get_document_link(doc.id).link.startswith("http")
    finally:
        removed = documents.delete_document(doc.id)
    assert removed.document_id == doc.id
    assert doc.id not in {d.id for d in documents.get_user_documents()}


# --------------------------------------------------------------------------
# Jobs: read endpoints over one finished job (fixture archives it afterwards)
# --------------------------------------------------------------------------

@requires_key
def test_finished_job_is_listed(client, struct, finished_job):
    job, _ = finished_job
    jobs = stract_api.JobApi(client)
    assert job.id in {j.id for j in jobs.get_struct_jobs(struct.id)}
    assert job.id in {j.id for j in jobs.get_user_jobs()}
    assert jobs.get_job_by_id(job.id).struct_id == struct.id


@requires_key
def test_finished_job_documents(client, finished_job):
    job, doc = finished_job
    documents = stract_api.DocumentApi(client).get_documents_for_job(job.id)
    assert [d.id for d in documents] == [doc.id]

    job_docs = stract_api.JobDocumentApi(client).get_job_documents(job.id)
    assert len(job_docs) == 1
    assert job_docs[0].job_id == job.id
    assert job_docs[0].document_id == doc.id
    assert job_docs[0].status == stract_api.JobStatus.COMPLETE


@requires_key
def test_finished_job_document_output(client, finished_job):
    job, _ = finished_job
    job_docs_api = stract_api.JobDocumentApi(client)
    job_doc = job_docs_api.get_job_documents(job.id)[0]

    output = job_docs_api.get_job_document_output(job_doc.id)
    assert output is not None
    assert output.job_document_id == job_doc.id
    assert "hamburgers" in output.data


@requires_key
def test_finished_job_outputs(client, finished_job):
    job, doc = finished_job
    jobs = stract_api.JobApi(client)

    records = jobs.get_job_output_json(job.id)
    assert len(records) == 1
    assert "hamburgers" in records[0].additional_properties

    csv_text = jobs.get_job_output_csv(job.id)
    header, *rows = csv_text.strip().splitlines()
    assert "document_name" in header
    assert "hamburgers" in header
    assert len(rows) == 1


# --------------------------------------------------------------------------
# Jobs: create -> cancel -> archive
# --------------------------------------------------------------------------

@requires_key
def test_job_create_cancel_archive(client, struct):
    documents = stract_api.DocumentApi(client)
    jobs = stract_api.JobApi(client)

    doc = documents.upload_text_document_no_catalog(
        stract_api.UploadTextDocumentRequest(
            name=unique("cancel-job-doc"),
            text="Six hamburgers and nothing else",
        ),
        _request_timeout=REQUEST_TIMEOUT_SECONDS,
    )
    job = None
    try:
        job = jobs.create_job(
            struct.id,
            stract_api.CreateJobRequest(additional_instructions="", document_ids=[doc.id]),
            _request_timeout=REQUEST_TIMEOUT_SECONDS,
        )
        cancelled = jobs.cancel_job(job.id, _request_timeout=REQUEST_TIMEOUT_SECONDS)
        assert cancelled.id == job.id
        # If the job was fast enough to finish before the cancel landed, that's fine too.
        assert cancelled.status in TERMINAL_STATUSES
    finally:
        if job is not None:
            archived_id = jobs.archive_job(job.id)
            assert archived_id == job.id
        documents.delete_document(doc.id)


@requires_key
def test_job_over_catalog(client, struct):
    """create_job can target a whole catalog instead of individual documents."""
    catalogs = stract_api.CatalogApi(client)
    documents = stract_api.DocumentApi(client)
    jobs = stract_api.JobApi(client)

    catalog = catalogs.create_catalog(stract_api.CreateCatalogRequest(name=unique("sdk-job-catalog")))
    job = None
    try:
        documents.upload_text_document(
            catalog.id,
            stract_api.UploadTextDocumentRequest(name="catalog-order", text="Seven hamburgers"),
            _request_timeout=REQUEST_TIMEOUT_SECONDS,
        )
        job = jobs.create_job(
            struct.id,
            stract_api.CreateJobRequest(additional_instructions="", catalog_id=catalog.id),
            _request_timeout=REQUEST_TIMEOUT_SECONDS,
        )
        assert job.catalog_id == catalog.id
        # Don't pay for the analysis; the point is that the catalog was accepted.
        jobs.cancel_job(job.id, _request_timeout=REQUEST_TIMEOUT_SECONDS)
    finally:
        if job is not None:
            jobs.archive_job(job.id)
        catalogs.delete_catalog(catalog.id)

    assert catalog.id not in {c.id for c in catalogs.get_catalogs()}


# --------------------------------------------------------------------------
# Error handling on unknown IDs
# --------------------------------------------------------------------------

@requires_key
def test_unknown_ids_raise_api_exception(client):
    missing = uuid.uuid4()
    with pytest.raises(ApiException) as exc:
        stract_api.JobApi(client).get_job_by_id(missing)
    assert exc.value.status in (400, 401, 404)

    with pytest.raises(ApiException) as exc:
        stract_api.DocumentApi(client).delete_document(missing)
    assert exc.value.status in (400, 401, 404)

    with pytest.raises(ApiException) as exc:
        stract_api.FieldApi(client).delete_field(missing)
    assert exc.value.status in (400, 401, 404)
