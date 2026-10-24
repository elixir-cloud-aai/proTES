# Getting started

To quickly install the service for development/testing purposes, we recommend
deployment via [Docker Compose](https://docs.docker.com/compose/install/), as
described below. For more durable deployments on cloud native infrastructure,
we also provide a [Helm](https://helm.sh/) chart in the
[`deployment/`](https://github.com/elixir-cloud-aai/proTES/tree/dev/deployment)
directory (see [Deployment](deployment.md)). The [FOCA deployment
guide](https://foca.readthedocs.io/en/latest/deployment/) explains how FOCA
apps, such as this one, are configured with Docker Compose and Helm.

## Quick start

--8<-- "README.md:quick-start"

Only proTES is published, on the host's loopback interface (`127.0.0.1`);
MongoDB and RabbitMQ are only reachable from the containers. Their data is
kept in the Compose project's volumes `mongodb-data` and `rabbitmq-data`
(e.g., `protes_mongodb-data`, see `docker volume ls`). Earlier versions kept
the data elsewhere (see [Upgrading](upgrading.md#docker-compose)). The
comments in
[`docker-compose.yaml`](https://github.com/elixir-cloud-aai/proTES/blob/dev/docker-compose.yaml)
describe the settings, e.g., the RabbitMQ credentials.

To also start [Flower](https://flower.readthedocs.io/), a web UI to monitor
the Celery worker, at `http://localhost:5555`, set its credentials and enable
the `monitoring` profile:

```bash
FLOWER_BASIC_AUTH='<user>:<password>' docker compose --profile monitoring up -d
```

### Other useful commands

To shut down the service, run:

```bash
docker compose down
```

Add `--volumes` to also delete the database and the broker's data.

!!! note
    The Celery worker tracks the state of forwarded tasks until they finish.
    When it starts again, it resumes the tracking of stored unfinished
    tasks, so tasks that finish while the service is down get their final
    state once it is up again (see [Task
    tracking](operations.md#task-tracking)).

If you need to inspect the logs, call:

```bash
docker compose logs
```

## Usage

[Endpoints](api.md) can be probed with `curl`, as above, or tried in the
Swagger UI. See [Configuration](configuration.md) for the TES instances that
tasks are forwarded to, and to change the config of the Docker Compose setup.
Set the [service info](configuration.md#service-info) to describe your
deployment: the defaults are placeholders, and proTES logs a warning on
startup while `id` or `organization` still have them.

All endpoints except `GET /service-info`, the specification and the Swagger
UI require an access token of a trusted identity provider (see [Access
control](access-control.md)). The Docker Compose setup applies the config
overlay [`config.dev.yaml`](https://github.com/elixir-cloud-aai/proTES/blob/dev/config.dev.yaml):
set the issuer of your identity provider and map your groups to roles
there. To try the API without an identity provider, use the Compose setup of
the [smoke test](development.md), which brings a fake one.

## Troubleshooting: network MTU

Sometimes containers cannot reach external hosts, such as the TES instances,
because the Maximum Transmission Unit (MTU) of the container network is
larger than that of the host or network (e.g., when the host interface or an
overlay network or VPN uses a lower MTU). TCP connections may then hang or
time out when packets exceed the path MTU and ICMP "fragmentation needed"
messages are not delivered.

Quick checks:

- On the host, `ip link` shows the MTU of the physical interface (e.g.,
  `enp3s0`) and of existing bridges (`br-...`).
- In the container, check the connectivity with `curl`, and
  `/etc/resolv.conf`.
- Capture ICMP messages on the host while reproducing the failure, e.g.,
  `sudo tcpdump -n -i any host <container-ip> and icmp`.

To verify, temporarily lower the MTU of the container's interface (requires
access to the host):

```bash
PID=$(docker inspect -f '{{.State.Pid}}' <container>)
sudo nsenter -t $PID -n ip link set dev eth0 mtu 1400
```

If that restores the connectivity, the path MTU is likely the cause. To fix
it permanently, either:

- set the MTU of the Docker daemon (for all containers): add `{"mtu": 1400}`
  to `/etc/docker/daemon.json`, run `sudo systemctl restart docker` and
  recreate the networks; or
- set the MTU of the Compose project's network, in a
  `docker-compose.override.yaml`:

```yaml
networks:
  default:
    driver: bridge
    driver_opts:
      com.docker.network.driver.mtu: "1400"
```

Then recreate the Compose stack, so that the bridge and the virtual
interfaces are created with the new MTU, and verify it with `ip link` and a
`curl` from a container.
