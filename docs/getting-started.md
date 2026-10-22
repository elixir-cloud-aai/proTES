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

### Other useful commands

To shut down the service, run:

```bash
docker compose down
```

!!! warning
    The Celery worker tracks the state of forwarded tasks until they finish.
    Tracking is not resumed after a restart: tasks that are still being
    tracked (or queued for tracking in RabbitMQ) when the service is shut
    down keep the state last recorded. Shut down when no tasks are running.

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
