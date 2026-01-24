# Middleware Management API 

## Overview

This document describes the implementation of Subtask 1 for the Middleware Management feature in proTES. This subtask focuses on designing and documenting the API specification that will enable dynamic middleware management at runtime.

## Background

The proTES project required a way to manage middleware components dynamically without restarting the service. The maintainer requested that this feature be broken down into smaller, independently mergeable pull requests following a design-first approach. This subtask represents the foundation: the OpenAPI specification that defines how the API will behave.

## Implementation Details

### What Was Built

This subtask delivers a complete OpenAPI 3.0 specification for middleware management. The specification defines seven REST endpoints that cover all necessary operations for middleware lifecycle management.

### API Endpoints

**List Middlewares** - GET /ga4gh/tes/v1/middlewares
Returns all configured middlewares with pagination and filtering support. Results are sorted by execution order by default. Supports filtering by enabled status and source type.

**Add Middleware** - POST /ga4gh/tes/v1/middlewares
Creates a new middleware in the execution stack. Supports loading from local class paths or GitHub repositories. Automatically handles order assignment and stack shifting.

**Get Middleware Details** - GET /ga4gh/tes/v1/middlewares/{middleware_id}
Retrieves detailed information about a specific middleware including configuration, metadata, and execution statistics.

**Update Middleware** - PUT /ga4gh/tes/v1/middlewares/{middleware_id}
Updates middleware configuration. Only allows modification of name, order, config parameters, and enabled status. Class path cannot be changed for security reasons.

**Delete Middleware** - DELETE /ga4gh/tes/v1/middlewares/{middleware_id}
Removes a middleware from the stack. Supports soft delete (disable) by default and hard delete with force parameter.

**Reorder Stack** - PUT /ga4gh/tes/v1/middlewares/reorder
Reorders the entire middleware execution stack by accepting an ordered array of middleware IDs.

**Validate Code** - POST /ga4gh/tes/v1/middlewares/validate
Validates middleware code before creation. Checks Python syntax, required interface implementation, and security constraints.

### Data Model

The API uses nine schema definitions to structure request and response data:

**MiddlewareConfig**: Complete middleware representation including ID, name, class path, execution order, enabled status, configuration parameters, source information, and timestamps.

**MiddlewareCreate**: Request body for creating new middleware. Includes name, class path (string or array for fallback groups), optional order, enabled flag, configuration dict, and optional GitHub URL.

**MiddlewareUpdate**: Request body for updates. Limited to name, order, config, and enabled fields to prevent unauthorized code changes.

**MiddlewareList**: Paginated list response containing middleware array and total count.

**MiddlewareCreateResponse**: Response after successful creation including the new middleware object and a success message.

**MiddlewareOrder**: Request body for reordering containing an array of middleware IDs in desired execution order.

**ValidationRequest**: Code validation request containing Python code string to validate.

**ValidationResponse**: Validation result including validity boolean, validation messages array, detected class name, and required methods check.

**ErrorResponse**: Standard error response with status code, error type, and detailed message.

### Key Design Decisions

**MongoDB ObjectId Format**: Uses 24-character hexadecimal strings for middleware identification. This aligns with the existing proTES database schema and provides guaranteed uniqueness.

**Order-Based Execution**: Middlewares execute in ascending order. Lower order values run first. This provides clear, predictable execution flow that's easy to understand and debug.

**Fallback Group Support**: Allows multiple class paths in a single middleware entry. If the first middleware fails, the system automatically tries the next one in the list. This improves reliability without complex error handling.

**Soft Delete Default**: DELETE operations disable rather than remove middlewares by default. This preserves execution history and allows easy rollback. Hard delete requires explicit force parameter.

**Immutable Class Path**: Once created, a middleware's class path cannot be changed. This prevents security risks from code substitution attacks. To change implementation, users must delete and recreate.

**GitHub Integration**: Supports loading middleware code directly from GitHub URLs. The system fetches, validates, and caches the code. This enables sharing middleware across deployments without manual file management.

**Source Tracking**: Records whether middleware originated from local files or GitHub. Helps administrators understand deployment composition and troubleshoot issues.

**Validation Endpoint**: Separate endpoint for validating middleware code before creation. Prevents deployment of broken middleware and provides immediate feedback on implementation issues.

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
└── middleware.md                           (This file)
```

## Testing Approach

This subtask focuses on specification validation rather than runtime testing since no executable code is implemented yet. Validation performed:

**YAML Syntax**: Verified file parses correctly as valid YAML without syntax errors.

**OpenAPI Compliance**: Confirmed specification follows OpenAPI 3.0 standards including required fields, valid schema definitions, and proper reference resolution.

**Schema Completeness**: Validated all endpoints reference defined schemas and all schemas include required properties with appropriate types.

**Path Coverage**: Verified all seven endpoints are defined with appropriate HTTP methods and parameters.

Runtime testing will occur in Subtask 2 when controllers are implemented.

## Security Considerations

The specification includes comprehensive security design:

**Authentication Required**: All middleware management endpoints require authentication through the existing proTES security scheme.

**Input Validation**: All parameters include type, format, and constraint definitions. Connexion automatically validates inputs before they reach controller code.

**MongoDB ObjectId Pattern**: Enforces 24-character hex pattern preventing injection attacks through malformed IDs.

**Class Path Immutability**: Prevents code substitution attacks by making class paths unchangeable after creation.

**Database Constraints**: Unique indexes on both name and class_path fields prevent duplicate middleware registration.

**Source Tracking**: Records code origin for audit and security review purposes.

Authorization controls and role-based access will be added in Subtask 4.

## Future Work

This subtask completes the API design phase. Subsequent subtasks will build on this foundation:

**Subtask 2**: Implement controller logic to handle API requests and interact with MongoDB.

**Subtask 3**: Build dynamic middleware loading system that instantiates classes and manages execution stack at runtime.

**Subtask 4**: Add authentication, authorization, RBAC controls, and API security.

**Subtask 5**: Implement monitoring, logging, and metrics collection for middleware operations.

**Subtask 6**: Complete integration testing, update deployment configurations, and finalize documentation.

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

## Breaking Changes

None. This subtask only adds new API endpoints without modifying existing functionality.

## Validation Results

Specification validated successfully:
- 7 API endpoints defined
- 9 schema definitions complete
- All references resolve correctly
- YAML syntax valid
- OpenAPI 3.0 compliance confirmed
Location: pro_tes/api/middleware_management.yaml

## Changelog

**2026-01-24**: Initial OpenAPI specification completed
- Defined 7 REST endpoints for middleware management
- Created 9 schema definitions
- Integrated with FOCA configuration
- Delivered comprehensive documentation suite
- Validated specification structure and syntax
