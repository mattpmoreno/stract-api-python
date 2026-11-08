"""Live tests for the generated SDK, following the flows in README.md.

These call the real Stract API and spend a small amount of credits.
STRACT_API_KEY is read from the .env file in the repo root.
"""
import asyncio
import os
import time
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv
from pydantic import ValidationError

import stract_api
from stract_api import ApiException

load_dotenv(Path(__file__).parent.parent / ".env")

API_KEY = os.environ.get("STRACT_API_KEY")

requires_key = pytest.mark.skipif(not API_KEY, reason="STRACT_API_KEY not set in .env")

BATCH_TIMEOUT_SECONDS = 300
POLL_INTERVAL_SECONDS = 5


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

@pytest.fixture(scope="session")
def client():
    config = stract_api.Configuration(access_token=API_KEY)
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

def test_invalid_api_key_raises_api_exception():
    config = stract_api.Configuration(access_token="not-a-real-key")
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
        file=("order-1044.txt", b"One hamburger and two milkshakes")
    )
    try:
        job = jobs.create_job(
            struct.id,
            stract_api.CreateJobRequest(additional_instructions="", document_ids=[doc.id]),
        )

        deadline = time.monotonic() + BATCH_TIMEOUT_SECONDS
        current = job
        while not current.is_complete:
            assert time.monotonic() < deadline, "Batch job did not finish in time"
            time.sleep(POLL_INTERVAL_SECONDS)
            current = jobs.get_job_by_id(job.id)

        assert current.status == stract_api.JobStatus.COMPLETE

        records = jobs.get_job_output_json(job.id)
        assert len(records) == 1
        assert "hamburgers" in records[0].additional_properties
    finally:
        documents.delete_document(doc.id)


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
