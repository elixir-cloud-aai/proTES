# Middleware Management API 

## Overview

The Middleware Management API provides REST endpoints for dynamically managing middleware components in proTES at runtime. This API allows administrators and developers to add, configure, update, and remove middleware without service restarts.

## Background

proTES uses a middleware architecture to process task execution requests. Previously, middleware configuration was static and required service restarts for any changes. The Middleware Management API enables dynamic runtime configuration, making it easier to adapt the service to changing requirements and deploy new middleware components.

## API Specification

The Middleware Management API is defined using OpenAPI 3.0 specification and provides seven REST endpoints for complete middleware lifecycle management.

### API Endpoints

**List Middlewares** - GET /protes/v1/middlewares
Returns all configured middlewares with pagination and filtering support. Results are sorted by execution order by default. Supports filtering by enabled status and source type.

**Add Middleware** - POST /protes/v1/middlewares
Creates a new middleware in the execution stack. Supports loading from local packages, GitHub repositories, or PyPI packages. Automatically handles order assignment and stack shifting.

**Get Middleware Details** - GET /protes/v1/middlewares/{middleware_id}
Retrieves detailed information about a specific middleware including configuration, metadata, and execution statistics.

**Update Middleware** - PUT /protes/v1/middlewares/{middleware_id}
Updates middleware configuration. Only allows modification of name, order, config parameters, and enabled status. Package path and entry point cannot be changed for security reasons.

**Delete Middleware** - DELETE /protes/v1/middlewares/{middleware_id}
Removes a middleware from the stack. Supports soft delete (disable) by default and hard delete with force parameter.

**Reorder Stack** - PUT /protes/v1/middlewares/reorder
Reorders the entire middleware execution stack by accepting an ordered array of middleware IDs.

**Validate Code** - POST /protes/v1/middlewares/validate
Validates middleware code before creation. Checks Python syntax, required interface implementation, and security constraints.

### Data Model

The API uses comprehensive schema definitions to structure request and response data:

**MiddlewareConfig**: Complete middleware representation including ID, name, package information (source type, package path, entry point), execution order, enabled status, configuration parameters, and timestamps.

**MiddlewareCreate**: Request body for creating new middleware. Includes name, package source configuration (local path, GitHub URL, or PyPI package), entry point (class path), optional order, enabled flag, and configuration dict.

**MiddlewareUpdate**: Request body for updates. Limited to name, order, config, and enabled fields to prevent unauthorized code changes.

**MiddlewareList**: Paginated list response containing middleware array, total count, page information, and navigation tokens following GA4GH pagination guidelines.

**MiddlewareCreateResponse**: Response after successful creation including the middleware ID, assigned order, and success message.

**MiddlewareOrder**: Request body for reordering containing an array of middleware IDs in desired execution order.

**ValidationRequest**: Code validation request containing package source information and entry point to validate.

**ValidationResponse**: Validation result including validity boolean, validation messages, error details with line numbers, and warnings.

**ErrorResponse**: Standard error response with HTTP status code, error message, and optional details.

### Key Features

**MongoDB ObjectId Format**: Uses 24-character hexadecimal strings for middleware identification. This aligns with the existing proTES database schema and provides guaranteed uniqueness.

**Order-Based Execution**: Middlewares execute in ascending order. Lower order values run first. This provides clear, predictable execution flow that's easy to understand and debug.

**Fallback Group Support**: Allows grouping multiple middleware sources in a single middleware entry. If the first middleware fails, the system automatically tries the next one in the list. Each middleware in a fallback group specifies its own source, package path, and entry point.

**Soft Delete Default**: DELETE operations disable rather than remove middlewares by default. This preserves execution history and allows easy rollback. Hard delete requires explicit force parameter.

**Immutable Package Configuration**: Once created, a middleware's package source and entry point cannot be changed. This prevents security risks from code substitution attacks. To change implementation, users must delete and recreate.

**Multiple Package Sources**: Supports loading middleware from:
  - **Local packages**: Installed Python packages with a class path entry point
  - **GitHub repositories**: Direct Git repository URLs with setup.py or pyproject.toml
  - **PyPI packages**: Public or private package registries with specified entry points

**Source Tracking**: Records whether middleware originated from local packages, GitHub, or PyPI. Helps administrators understand deployment composition and troubleshoot issues.

**Validation Endpoint**: Separate endpoint for validating middleware code before creation. Prevents deployment of broken middleware and provides immediate feedback on implementation issues.

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

### Adding a Local Package Middleware

```bash
curl -X POST https://protes.example.org/protes/v1/middlewares \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Distance-based Router",
    "source": {
      "type": "local",
      "entry_point": "pro_tes.plugins.middlewares.task_distribution.distance.TaskDistributionDistance"
    },
    "order": 0,
    "enabled": true
  }'
```

### Adding a GitHub Middleware

```bash
curl -X POST https://protes.example.org/protes/v1/middlewares \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Custom Load Balancer",
    "source": {
      "type": "github",
      "repository": "https://github.com/user/repo.git",
      "entry_point": "custom_middleware.LoadBalancer"
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
    "sources": [
      {
        "type": "local",
        "entry_point": "pro_tes.plugins.middlewares.task_distribution.distance.TaskDistributionDistance"
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
