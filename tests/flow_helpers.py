"""Build form submissions with the values displayed by Home Assistant."""


def suggested_form_values(schema):
    """Accept the displayed ID suggestion instead of simulating a cleared ID."""
    values = schema({})
    for marker in schema.schema:
        if marker == "entity_id" and marker.description:
            values["entity_id"] = marker.description["suggested_value"]
    return values
