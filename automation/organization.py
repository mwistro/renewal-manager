"""Validate a user-supplied, single-tenant Microsoft application configuration."""
from uuid import UUID


def require_organization(config):
    for field in ('tenant_id', 'client_id'):
        value = config.get(field, '')
        try:
            parsed = UUID(value)
            if not parsed.int or str(parsed) != value.lower():
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError(f'Configura {field} con el UUID de tu propia aplicación Microsoft.') from None
    return config
