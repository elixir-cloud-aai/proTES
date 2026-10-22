# Access control

## Authentication

Authentication is disabled for the TES API for now (`disable_auth` in
`api.specs` of the config): anyone who can reach the API can create, list,
read and cancel all tasks. Run proTES only where its clients are trusted,
e.g., behind a reverse proxy that authenticates them, or for development.
Authentication with OpenID Connect access tokens of trusted issuers and
owner-based access to tasks are planned
([#201](https://github.com/elixir-cloud-aai/proTES/issues/201)).

## Credentials of TES instances

If a request to `POST /tasks` has HTTP Basic credentials (an `Authorization:
Basic` header), proTES stores them with the task and uses them for all
requests to the TES instance that the task is forwarded to: to create, track
and cancel it. Credentials in the URLs of inputs and outputs
(`https://user:password@host/...`) are removed before the task is forwarded,
unless the TES instance is a [Funnel](https://ohsu-comp-bio.github.io/funnel/)
instance.
