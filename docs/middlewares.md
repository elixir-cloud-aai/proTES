# Middlewares

Middlewares process each new task (`POST /tasks`) before proTES forwards it:
they select the TES instances that the task is forwarded to, and in which
order, and may change the request. The stack of middlewares is configured in
`custom.middlewares` (see [Middlewares](configuration.md#middlewares)): a
list of steps, each a list of alternative middlewares, of which the first one
that can be applied is used.

## Built-in middlewares

Two middlewares are shipped with proTES, as proofs of concept for different
ways of distributing tasks. Both forward to the TES instances of
`custom.tes.service_list` and those discovered in a service registry (see
[TES instances](configuration.md#tes-instances)).

- **Bringing compute to the data:**
  `pro_tes.plugins.middlewares.task_distribution.distance.TaskDistributionDistance`
  orders the TES instances by the distance that the task's input data has to
  travel to them: the sum of the geodesic distances between the instance and
  each input (`inputs[].url`); instances at the same distance keep the order
  of the base class (see below). Locations are looked up for each task, from
  the IP addresses of the hosts: the free API of [DB-IP](https://db-ip.com/)
  gives the place of an IP address (city, region and country), and
  [Nominatim](https://nominatim.org/) of
  [OpenStreetMap](https://www.openstreetmap.org/) its coordinates. Both are
  called over HTTPS, and thus learn the IP addresses of the hosts (DB-IP) and
  their places (Nominatim); mind their usage limits (e.g., DB-IP's daily
  requests, and Nominatim's one request per second). Addresses that are not
  global (e.g., private ones, of hosts in the same cluster) are not sent
  to them. The middleware can't be applied to tasks without input URLs, or if
  a location can't be determined, e.g., of such an address.
- **Load balancing:**
  `pro_tes.plugins.middlewares.task_distribution.random.TaskDistributionRandom`
  orders the TES instances randomly.

The base config applies the distance-based middleware, with the random one as
its fallback.

## Writing middlewares

A middleware is a class that inherits from
[`AbstractMiddleware`](reference.md#pro_tes.middleware.abstract_middleware.AbstractMiddleware)
and implements `apply_middleware()`, which receives the request (a
`flask.Request`) and returns it, possibly changed. To select TES instances,
set the key `tes_urls` of the request's JSON body to the list of their URLs,
in the order in which they are tried; proTES removes the key before it
forwards the task, and skips URLs that are neither in
`custom.tes.service_list` nor discovered (it has no credentials for them).
Credentials that middlewares add to the URLs of inputs and outputs are
removed. Raise an exception if the middleware can't be applied, so that the
next alternative of the step is tried. The task distribution middlewares
derive from
[`TaskDistributionBaseClass`](reference.md#pro_tes.plugins.middlewares.task_distribution.base.TaskDistributionBaseClass),
which sets `tes_urls` from the URLs of `custom.tes.service_list` and the
discovered TES instances, once each, in that order; override its
`_set_tes_urls()` to order them differently.

Middlewares are imported by their import path, so they can live in any
package installed next to proTES, e.g., in a custom image based on the proTES
image. List them in `custom.middlewares`.
