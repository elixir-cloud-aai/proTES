"""proTES integration tests."""

import requests

# Timeout of requests to the app, in seconds
TIMEOUT = 30
tes_url = "http://localhost:8080/ga4gh/tes/v1"
tasks_body = {"executors": [{"image": "alpine", "command": ["echo", "hello"]}]}
headers = {"accept": "application/json", "Content-Type": "application/json"}


# test for POST /tasks endpoint
def test_post_tasks_200() -> None:
    """Test POST /tasks for successful task creation."""
    post_response = requests.post(
        timeout=TIMEOUT, url=f"{tes_url}/tasks", headers=headers, json=tasks_body
    )
    assert post_response.status_code == 200
    assert post_response.json()["id"]


# tests for GET /tasks
def test_get_tasks_minimal_200() -> None:
    """Test GET /tasks for successful fetching of all tasks."""
    params = {"view": "MINIMAL"}
    response = requests.get(timeout=TIMEOUT, url=f"{tes_url}/tasks", params=params)
    assert response.status_code == 200
    tasks_response = response.json()["tasks"]
    next_page_token = response.json()["next_page_token"]
    if tasks_response == []:
        assert next_page_token == ""
    else:
        first_tasks_response = tasks_response[0]
        assert next_page_token
        assert first_tasks_response["id"]
        assert first_tasks_response["state"]


def test_get_tasks_basic_200() -> None:
    """Test GET /tasks for successful fetching of all tasks."""
    params = {"view": "BASIC"}
    response = requests.get(timeout=TIMEOUT, url=f"{tes_url}/tasks", params=params)
    assert response.status_code == 200
    tasks_response = response.json()["tasks"]
    next_page_token = response.json()["next_page_token"]
    if tasks_response == []:
        assert next_page_token == ""
    else:
        first_tasks_response = tasks_response[0]
        first_tasks_response_executors = first_tasks_response["executors"][0]
        assert next_page_token
        assert first_tasks_response["id"]
        assert first_tasks_response["state"]
        assert first_tasks_response["executors"]
        assert first_tasks_response_executors["image"]
        assert first_tasks_response_executors["command"]


def test_get_tasks_full_200() -> None:
    """Test GET /tasks for successful fetching of all tasks."""
    params = {"view": "FULL"}
    response = requests.get(timeout=TIMEOUT, url=f"{tes_url}/tasks", params=params)
    assert response.status_code == 200
    tasks_response = response.json()["tasks"]
    next_page_token = response.json()["next_page_token"]
    if tasks_response == []:
        assert next_page_token == ""
    else:
        first_tasks_response = tasks_response[0]
        first_tasks_response_executors = first_tasks_response["executors"][0]
        assert next_page_token
        assert first_tasks_response["id"]
        assert first_tasks_response["state"]
        assert first_tasks_response["executors"]
        assert first_tasks_response_executors["image"]
        assert first_tasks_response_executors["command"]


# test for GET /tasks/{id}
def test_get_task_by_id_minimal() -> None:
    """Test GET /tasks/{id} with the minimal view."""
    post_response = requests.post(
        timeout=TIMEOUT, url=f"{tes_url}/tasks", headers=headers, json=tasks_body
    )
    id = (post_response.json()["id"],)
    response = requests.get(
        timeout=TIMEOUT, url=f"{tes_url}/tasks/{id[0]}", params={"view": "MINIMAL"}
    )
    assert response.status_code == 200
    assert response.json()["id"] == id[0]
    assert response.json()["state"]


def test_get_task_by_id_basic() -> None:
    """Test GET /tasks/{id} with the basic view."""
    post_response = requests.post(
        timeout=TIMEOUT, url=f"{tes_url}/tasks", headers=headers, json=tasks_body
    )
    id = post_response.json()["id"]
    response = requests.get(
        timeout=TIMEOUT, url=f"{tes_url}/tasks/{id}", params={"view": "BASIC"}
    )
    assert response.status_code == 200
    assert response.json()["id"] == id
    assert response.json()["state"]
    assert response.json()["executors"]
    assert response.json()["executors"][0]["image"]
    assert response.json()["executors"][0]["command"]


def test_get_task_by_id_full() -> None:
    """Test GET /tasks/{id} with the full view."""
    post_response = requests.post(
        timeout=TIMEOUT, url=f"{tes_url}/tasks", headers=headers, json=tasks_body
    )
    id = post_response.json()["id"]
    response = requests.get(
        timeout=TIMEOUT, url=f"{tes_url}/tasks/{id}", params={"view": "FULL"}
    )
    assert response.status_code == 200
    assert response.json()["id"] == id
    assert response.json()["state"]
    assert response.json()["executors"]
    assert response.json()["executors"][0]["image"]
    assert response.json()["executors"][0]["command"]


# test to GET /service-info
def test_get_service_info_200() -> None:
    """Test GET /service-info."""
    response = requests.get(timeout=TIMEOUT, url=f"{tes_url}/service-info")
    assert response.status_code == 200


# test for POST /tasks/{id}:cancel
def test_cancel_task_200() -> None:
    """Test POST /tasks/{id}:cancel."""
    post_response = requests.post(
        timeout=TIMEOUT, url=f"{tes_url}/tasks", headers=headers, json=tasks_body
    )
    id = post_response.json()["id"]
    response = requests.post(
        timeout=TIMEOUT,
        url=f"{tes_url}/tasks/{id}:cancel",
    )
    assert response.status_code == 200
    # may need to wait long for next test as task may be stay in queue
    # and may still fail for no fault of ours
    # for _ in range(60):
    #     response_status = requests.get(timeout=TIMEOUT,
    #         url=f"{tes_url}/tasks/{id}", params={"view": "MINIMAL"}
    #     )
    #     sleep(5)
    #     if response_status.json()["state"] == "CANCELED":
    #         break
    # assert response_status.json()["state"] == "CANCELED"
