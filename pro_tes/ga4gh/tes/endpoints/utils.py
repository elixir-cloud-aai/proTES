def 
    # Set access token
    if authorization required:
        try:
            access_token = request_access_token(
                user_id=document['user_id'],
                token_endpoint=endpoint_params['token_endpoint'],
                timeout=endpoint_params['timeout_token_request'],
            )
            validate_token(
                token=access_token,
                key=security_params['public_key'],
                identity_claim=security_params['identity_claim'],
            )
        except Exception as e:
            logger.exception(
                (
                    'Could not get access token from token endpoint '
                    "'{token_endpoint}'. Original error message {type}: {msg}"
                ).format(
                    token_endpoint=endpoint_params['token_endpoint'],
                    type=type(e).__name__,
                    msg=e,
                )
            )
            raise Forbidden
    else:
        access_token = None
