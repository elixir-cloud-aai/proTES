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

### Key Features

**Order-Based Execution**: Middlewares execute in ascending order. Lower order values run first. This provides clear, predictable execution flow that's easy to understand and debug.

**Fallback Group Support**: Allows grouping multiple middleware sources in a single middleware entry. If the first middleware fails, the system automatically tries the next one in the fallback group. Each middleware in a fallback group specifies its own source, package path, and entry point.

**Immutable Package Configuration**: Once created, a middleware's package source and entry point cannot be changed. This prevents security risks from code substitution attacks. To change implementation, users must delete and recreate.

**Multiple Package Sources**: Supports loading middleware from:
  - **GitHub repositories**: Git repository URLs with setup.py or pyproject.toml (recommended for production)
  - **PyPI packages**: Public or private package registries with specified entry points (recommended for production)
  - **Local packages**: Installed Python packages with a class path entry point (**deprecated** - for development purposes only, will be removed in future versions)

**Note on Local Packages**: Local package sources are discouraged and deprecated. They are difficult to reproduce across environments and most users won't have access to the running instance's file system. This option is only useful for developers and local deployments and will be removed once the built-in middlewares are migrated to a separate repository.

**Source Tracking**: Records whether middleware originated from GitHub or PyPI. Helps administrators understand deployment composition and troubleshoot issues.

**GA4GH-Compliant Pagination**: Implements page-based pagination following the GA4GH API pagination guide with `page` and `page_size` parameters, supporting predictable result navigation.

### Integration with FOCA

The specification integrates with proTES's existing FOCA configuration framework. Added configuration block:

```yaml
specs:
  - path:
      - api/middleware_management.yaml
    add_operation_fields:
      x-openapi-router-controller: pro_tes.api.middlewares.controllers
    connexion:
      strict_validation: True
      validate_responses: True
```

This configuration tells FOCA to:
- Load the OpenAPI spec from the api directory
- Route requests to the middlewares controller module
- Use existing authentication scheme for security
- Enable strict validation of requests and responses

The FOCA framework uses Connexion under the hood, which automatically generates routing, parameter validation, and response serialization based on the OpenAPI specification.

## File Structure

```
pro_tes/
├── api/
│   └── middleware_management.yaml          (OpenAPI specification)
└── config.yaml                             (FOCA integration)

docs/
└── middleware.md                           (This documentation)
```

## Usage Examples

### Adding a GitHub Middleware

```bash
curl -X POST https://protes.example.org/protes/v1/middlewares \
  -H "Content-Type: application/json" \
  -d '{
    "source": {
      "type": "github",
      "repository": "https://github.com/user/repo.git",
      "entry_point": "custom_middleware.LoadBalancer"
    },
    "order": 0,
    "enabled": true
  }'
```

### Adding a PyPI Package Middleware

```bash
curl -X POST https://protes.example.org/protes/v1/middlewares \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Third-party Middleware",
    "source": {
      "type": "pypi",
      "package": "protes-middleware-custom",
      "entry_point": "custom.Middleware",
      "version": "1.0.0"
    },
    "enabled": true
  }'
```

### Creating a Fallback Group

```bash
curl -X POST https://protes.example.org/protes/v1/middlewares \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Load Balancing Group",
    "source": [
      {
        "type": "github",
        "repository": "https://github.com/org/primary.git",
        "entry_point": "primary.DistanceRouter"
      },
      {
        "type": "github",
        "repository": "https://github.com/org/fallback.git",
        "entry_point": "fallback.RandomRouter"
      }
    ],
    "order": 0,
    "enabled": true
  }'
```

### Disabling a Middleware (Instead of Deleting)

```bash
curl -X PUT https://protes.example.org/protes/v1/middlewares/{middleware_id} \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": false
  }'
```

## Security Considerations

The API includes comprehensive security controls:

**Authentication Required**: All middleware management endpoints require authentication through the existing proTES security scheme.

**Input Validation**: All parameters include type, format, and constraint definitions. The API framework automatically validates inputs before processing.

**MongoDB ObjectId Pattern**: Enforces 24-character hex pattern preventing injection attacks through malformed IDs.

**Package Configuration Immutability**: Prevents code substitution attacks by making package sources and entry points unchangeable after creation.

**Database Constraints**: Unique indexes on both name and entry point fields prevent duplicate middleware registration.

**Source Tracking**: Records code origin for audit and security review purposes.

**Error Responses**: All endpoints define 400 (Bad Request), 401 (Unauthorized), 403 (Forbidden), and where applicable 404 (Not Found) error responses for comprehensive error handling.

## Dependencies

**External**:
- OpenAPI 3.0 specification format
- FOCA framework (Flask-based configuration)
- Connexion (OpenAPI request routing)
- MongoDB (persistence layer)

**Internal**:
- Existing proTES API structure
- Current middleware plugin architecture
- MongoDB database configuration

## API Specification Location

The complete OpenAPI 3.0 specification is available at: `pro_tes/api/middleware_management.yaml`
