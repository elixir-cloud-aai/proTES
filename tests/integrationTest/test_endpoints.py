import requests

tes_url = "http://localhost:8080/ga4gh/tes/v1"
tasks_body = {
    "executors": [
        {
            "image": "alpine",
            "command": [
                "echo",
                "hello"
            ]
        }
    ]
}
headers = {
    'accept': 'application/json',
    'Content-Type': 'application/json'
}


# test for POST /tasks endpoint
def test_post_tasks_200():
    """ Test POST /tasks for successful task creation"""
    post_response = requests.post(
        url=f"{tes_url}/tasks",
        headers=headers,
        json=tasks_body
    )
    assert post_response.status_code == 200
    # checks if id is present in response
    assert post_response.json()['id']


# tests for GET /tasks
def test_get_tasks_minimal_200():
    """Test GET /tasks for successful fetching of all tasks"""
    params = {
        'view': 'MINIMAL'
    }
    response = requests.get(
        url=f"{tes_url}/tasks",
        params=params
    )
    assert response.status_code == 200
    tasks_response = response.json()['tasks']
    next_page_token = response.json()['next_page_token']
    # if the tasks list is empty
    if tasks_response == []:
        assert next_page_token == ''
    else:
        # for accessing the very first task from tasks list
        first_tasks_response = tasks_response[0]
        assert next_page_token
        assert first_tasks_response['id']
        assert first_tasks_response['state']


def test_get_tasks_basic_200():
    """Test GET /tasks for successful fetching of all tasks"""
    params = {
        'view': 'BASIC'
    }
    response = requests.get(
        url=f"{tes_url}/tasks",
        params=params
    )
    assert response.status_code == 200
    tasks_response = response.json()['tasks']
    next_page_token = response.json()['next_page_token']
    if tasks_response == []:
        assert next_page_token == ''
    else:
        first_tasks_response = tasks_response[0]
        first_tasks_response_executors = first_tasks_response['executors'][0]
        # checks all required parameters in response body
        assert next_page_token
        assert first_tasks_response['id']
        assert first_tasks_response['state']
        assert first_tasks_response['executors']
        assert first_tasks_response_executors['image']
        assert first_tasks_response_executors['command']


def test_get_tasks_full_200():
    """Test GET /tasks for successful fetching of all tasks"""
    params = {
        'view': 'FULL'
    }
    response = requests.get(
        url=f"{tes_url}/tasks",
        params=params
    )
    assert response.status_code == 200
    tasks_response = response.json()['tasks']
    next_page_token = response.json()['next_page_token']
    if tasks_response == []:
        assert next_page_token == ''
    else:
        first_tasks_response = tasks_response[0]
        first_tasks_response_executors = first_tasks_response['executors'][0]
        assert next_page_token
        assert first_tasks_response['id']
        assert first_tasks_response['state']
        assert first_tasks_response['executors']
        assert first_tasks_response_executors['image']
        assert first_tasks_response_executors['command']


# test for GET /tasks/{id}
def test_get_task_by_id_minimal():
    post_response = requests.post(
        url=f"{tes_url}/tasks",
        headers=headers,
        json=tasks_body
    )
    id = post_response.json()['id'],
    response = requests.get(
        url=f"{tes_url}/tasks/{id[0]}",
        params={
            'view': 'MINIMAL'
        }
    )
    assert response.status_code == 200
    assert response.json()['id']
    assert response.json()['state']


def test_get_task_by_id_basic():
    post_response = requests.post(
        url=f"{tes_url}/tasks",
        headers=headers,
        json=tasks_body
    )
    id = post_response.json()['id']
    response = requests.get(
        url=f"{tes_url}/tasks/{id}",
        params={
            'view': 'BASIC'
        }
    )
    assert response.status_code == 200
    assert response.json()['id']
    assert response.json()['state']
    assert response.json()['executors']
    assert response.json()['executors'][0]['image']
    assert response.json()['executors'][0]['command']


def test_get_task_by_id_full():
    post_response = requests.post(
        url=f"{tes_url}/tasks",
        headers=headers,
        json=tasks_body
    )
    id = post_response.json()['id']
    response = requests.get(
        url=f"{tes_url}/tasks/{id}",
        params={
            'view': 'FULL'
        }
    )
    assert response.status_code == 200
    assert response.json()['id']
    assert response.json()['state']
    assert response.json()['executors']
    assert response.json()['executors'][0]['image']
    assert response.json()['executors'][0]['command']


# test to GET /service-info
def test_get_service_info_200():
    response = requests.get(
        url=f"{tes_url}/service-info"
    )
    assert response.status_code == 200


# test for POST /tasks/{id}:cancel
def test_cancel_task_200():
    post_response = requests.post(
        url=f"{tes_url}/tasks",
        headers=headers,
        json=tasks_body
    )
    id = post_response.json()['id']
    response = requests.post(
        url=f"{tes_url}/tasks/{id}:cancel",
    )
    # here we could also check the state of task if it\
    # canceled or not
    assert response.status_code == 200
