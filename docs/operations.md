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

## Task owners

Only the owner of a task can read and cancel it through the API, not admins
(see [Task ownership](access-control.md#task-ownership)). Operators work on
the database instead (database `taskStore`, or the one in `MONGO_DBNAME`),
e.g., with `mongosh`; the owner of a task is in `user_id` of its document in
the collection `tasks`, and as owner roles (`g2` rules) in the collection
`policy_rules`:

```js
// tasks of a user
db.tasks.find({ user_id: "<sub>" }, { "task.id": 1, "task.state": 1 })
// tasks without owner, e.g., created by earlier versions
db.tasks.find({ user_id: { $exists: false } }, { "task.id": 1 })
```

Tasks created before owners were recorded have no owner, so nobody can see
them through the API. To give such a task to a user, add the two owner roles
via the [permissions API](access-control.md#permissions-api), as an admin:

```bash
for resource in "/ga4gh/tes/v1/tasks/<id>" "/ga4gh/tes/v1/tasks/<id>:cancel"; do
  curl -X POST "http://localhost:8080/admin/access-control/permissions" \
    -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
    -d "{\"policy_type\": \"g2\", \"rule\": {\"v0\": \"user:<sub>\", \"v1\": \"owner\", \"v2\": \"${resource}\"}}"
done
```

and set `user_id` of the task to the user's `sub` in the database. To purge
tasks, delete their documents and their owner roles, e.g., those without
owner:

```js
db.tasks.deleteMany({ user_id: { $exists: false } })
```

Owner roles left by deleted tasks don't give access to anything (owners get
`404 Not Found`), but are loaded on each request; delete them via the
permissions API, or in the database
(`db.policy_rules.deleteMany({ ptype: "g2", v2: { $in: [...] } })`, with the
paths of the deleted tasks, and those ending in `:cancel`).

## Abusive tasks

Admins can't cancel the tasks of others through the API. To stop a task,
cancel it at the TES instance that runs it: its URL is in
`tes_endpoint.host` of the task document, and its identifier there in
`tes_endpoint.task_id`; the operators of the TES instance can also cancel
it on their side. If a worker still tracks the task, proTES records its new
state; otherwise, set `task.state` of the document to `CANCELED`. To keep a
user from creating more tasks, remove them from the groups with the role
`user` at the identity provider; to keep proTES from sending tasks to a TES
instance, remove it from `custom.tes.service_list`.
