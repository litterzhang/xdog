def setup(api):
    async def ask(args, context):
        color = await api.select("Choose color", ["Red", "Blue"], message="Extension input is not tool approval")
        name = await api.input("Name", message="Editable extension input", default="seed")
        yes = await api.confirm("Proceed", "This does not grant host tool permissions")
        return f"DIALOG-RESULT-{color}-{name}-{yes}"

    async def callback(params, context):
        value = await api.input("Tool input", message="Tool approval still applies separately")
        return "TOOL-DIALOG-" + str(value)

    async def timeout(args, context):
        value = await api.input("Expires", timeout=0.1)
        return "TIMEOUT-" + str(value)

    async def form(args, context):
        import json

        value = await api.form(
            "Configure task",
            [
                {
                    "name": "name",
                    "kind": "text",
                    "label": "Task name",
                    "required": True,
                    "min_length": 1,
                    "description": "The task name is returned to this extension as data. "
                    "Choosing a name does not start a model turn, authorize tools, "
                    "change host permissions, or switch sessions. "
                    "Review all of the remaining field values before submitting the complete form.",
                },
                {"name": "count", "kind": "integer", "label": "Count", "default": 0, "minimum": 0, "maximum": 10},
                {"name": "enabled", "kind": "boolean", "label": "Enabled", "default": False},
                {
                    "name": "mode",
                    "kind": "choice",
                    "label": "Mode",
                    "default": None,
                    "choices": [{"label": "No mode", "value": None}, {"label": "Zero mode", "value": 0}],
                },
                {"name": "notes", "kind": "text", "label": "Notes", "multiline": True},
            ],
            message="Form answers are task data, not host tool approval",
            timeout=0.1 if args == "timeout" else 300,
        )
        return "FORM-RESULT-" + json.dumps(value, ensure_ascii=False, sort_keys=True)

    async def form_tool(params, context):
        return await form("", context)

    api.command("form", "Ask typed multi-field form", form)
    api.command("ask", "Ask select/input/confirm", ask)
    api.command("timeout", "Short dialog deadline", timeout)

    async def many(args, context):
        import json
        result = await api.form('Choose several values', [
            {'name': 'values', 'kind': 'multi_choice', 'required': True, 'min_items': 1, 'max_items': 3,
             'description': 'Review every selected typed value. Boolean false, integer zero and explicit null are '
                            'different values and are returned without coercion. Empty and omitted arrays also differ. '
                            'Selecting task data does not start a model turn, switch session, approve a tool call or '
                            'change host permissions. The whole form must be visibly reviewed before submission.',
             'choices': [{'label': 'Boolean false', 'value': False}, {'label': 'Integer zero', 'value': 0},
                         {'label': 'Explicit null', 'value': None}]},
            {'name': 'empty', 'kind': 'multi_choice', 'default': [],
             'choices': [{'label': 'Optional null', 'value': None}]}],
            message='Select distinct typed task data. Review every field; this is not tool approval.')
        return 'MANY-RESULT-' + json.dumps(result, sort_keys=True)

    api.command('many', 'Ask reviewed typed multi-selection form', many)
    async def many_tool(params, context):
        return await many('', context)
