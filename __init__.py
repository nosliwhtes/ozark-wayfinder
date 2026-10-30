"""Hermes directory plugin. No startup hooks, ambient paths, or automatic writes."""
from dataclasses import fields
import json
from pathlib import Path

from .wayfinder import Settings, Wayfinder


def _service(ctx):
    values = {}
    for field in fields(Settings):
        value = ctx.get_config(field.name, None)
        if value is not None:
            values[field.name] = value
    if not values.get('vault_root'):
        raise ValueError('set vault_root to an explicit existing absolute directory')
    for key in ('vault_root', 'mnemosyne_db', 'qmd_workspace'):
        if key in values:
            values[key] = Path(values[key])
    return Wayfinder(Settings(**values))


def _handle(ctx, operation, args):
    def error(code, detail):
        return json.dumps({'error': {'code': code, 'detail': detail}})
    try:
        service = _service(ctx)
    except (ValueError, TypeError, OSError):
        return error('configuration_error', 'Check explicit paths, tier roots, scan limits and backend configuration.')
    if not isinstance(args, dict):
        return error('invalid_input', 'Arguments must be an object.')
    try:
        if operation == 'recall':
            result = service.recall(args.get('query'), args.get('limit', 8))
        elif operation == 'checkpoint':
            result = service.checkpoint(args.get('path'), args.get('title'), args.get('summary'), args.get('confirm', False))
        else:
            result = service.health()
        return json.dumps(result, ensure_ascii=False)
    except (ValueError, TypeError):
        return error('invalid_input', 'Check arguments against the tool schema; paths must be safe vault-relative Markdown paths.')
    except FileExistsError:
        return error('target_exists', 'Existing notes are never replaced; select a new path.')
    except OSError:
        return error('filesystem_error', 'Cannot access the configured vault or publish the checkpoint.')
    except RuntimeError:
        return error('verification_failed', 'Checkpoint may exist but readback failed; inspect the target before retrying.')


def register(ctx):
    specifications = (
        ('recall', 'Recall canonical Markdown with optional isolated local QMD and read-only Mnemosyne evidence. Imported material stays imported.',
         {'query': {'type': 'string', 'minLength': 1, 'maxLength': 2000},
          'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50, 'default': 8}}, ['query']),
        ('checkpoint', 'Preview a create-only canonical Markdown note. Writes only with confirm=true after explicit user approval. Never reindexes QMD or writes Mnemosyne.',
         {'path': {'type': 'string', 'description': 'Vault-relative new .md path; tier comes from configured roots.'},
          'title': {'type': 'string', 'maxLength': 200}, 'summary': {'type': 'string', 'maxLength': 4000},
          'confirm': {'type': 'boolean', 'default': False}}, ['path', 'title', 'summary']),
        ('health', 'Probe configured backends locally and report disabled, degraded or error states without downloading models.', {}, []),
    )
    for operation, description, properties, required in specifications:
        name = 'wayfinder_' + operation
        def handler(args, operation=operation):
            return _handle(ctx, operation, args)
        ctx.register_tool(name=name, toolset='ozark_wayfinder',
                          schema={'name': name, 'description': description,
                                  'parameters': {'type': 'object', 'properties': properties,
                                                 'required': required, 'additionalProperties': False}},
                          handler=handler, description=description)
