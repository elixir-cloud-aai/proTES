# Operations

## Components

A deployment of proTES consists of:

- the app (the TES API), served by Gunicorn;
- one or more Celery workers, which track forwarded tasks at the TES
  instances (scale them up for many concurrent tasks: each worker process
  tracks one task at a time);
- RabbitMQ, the broker between the app and the workers;
- MongoDB, which stores the tasks (database `taskStore`, collection
  `tasks`);
- optionally Flower, a web UI to monitor the Celery workers.

The Docker Compose setup and the [Helm chart](deployment.md) run all of them.

## Logs

The app and the workers log to standard error (section `log` of the
config): each request to the API, the middlewares applied, the TES instances
that a task is forwarded to, and errors of the TES instances. Read them with
`docker compose logs` or `kubectl logs`.

## Task states

The state of a task is the state last tracked from the TES instance that runs
it (see [Tasks](api.md#tasks)). Tracking is not resumed after the workers or
RabbitMQ restart: tasks that were being tracked keep their last recorded
state.
