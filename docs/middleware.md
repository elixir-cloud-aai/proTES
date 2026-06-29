# Middleware Management API 

## Overview

The Middleware Management API provides REST endpoints for dynamically managing middleware components in proTES at runtime. This API allows administrators and developers to add, configure, update, and remove middleware without service restarts.

## Background

proTES uses a middleware architecture to process task execution requests. Previously, middleware configuration was static and required service restarts for any changes. The Middleware Management API enables dynamic runtime configuration, making it easier to adapt the service to changing requirements and deploy new middleware components.

## API Specification

The Middleware Management API is defined using OpenAPI 3.0 specification. For comprehensive, interactive documentation with the ability to explore endpoints, request/response schemas, and examples, please visit:

**[Swagger Editor - Middleware Management API](https://editor.swagger.io/?url=https://raw.githubusercontent.com/elixir-cloud-aai/proTES/refs/heads/dev/pro_tes/api/middleware_management.yaml)**

The interactive documentation provides:
- Complete endpoint definitions with request/response examples
- Detailed schema specifications for all data models
- Parameter descriptions and validation rules
- Error response definitions
- The ability to test API calls directly

## File Structure

```
pro_tes/
├── api/
│   └── middleware_management.yaml          (OpenAPI specification)
└── config.yaml                             (References the specification)

docs/
└── middleware.md                           (This documentation)
```

## Implementation Status

The middleware management controller layer is now implemented in
[`pro_tes/api/middlewares/controllers.py`](../pro_tes/api/middlewares/controllers.py)
and wired through the OpenAPI configuration in
[`pro_tes/config.yaml`](../pro_tes/config.yaml).

Unit tests for the middleware management controllers are expected under
tests/unitTest/pro_tes/api/middlewares/ (e.g. test_controllers.py).