"""Template Engine implementation."""

from typing import Any

from ploston_core.errors import create_error

from .filters import FILTERS
from .parser import (
    extract_all_references,
    extract_templates,
    has_templates,
    is_pure_template,
    validate_syntax,
)
from .types import RenderResult, TemplateContext


class TemplateEngine:
    """Render template expressions in workflow values.

    Supports:
    - Variable access: {{ inputs.url }}
    - Nested access: {{ steps.fetch.output.data }}
    - Filters: {{ items | length }}, {{ x | default(0) }}, {{ data | json }}

    Does NOT support:
    - Arbitrary Python expressions
    - Control flow (if/for)
    - Custom filters
    - Function calls
    """

    def __init__(self) -> None:
        """Initialize template engine."""
        self._filters = FILTERS

    def render(
        self,
        template: Any,
        context: TemplateContext,
    ) -> RenderResult:
        """Render template expressions in a value.

        Args:
            template: Value that may contain {{ }} expressions.
                      Can be string, dict, list, or primitive.
            context: Template context with inputs, steps, config

        Returns:
            RenderResult with rendered value

        Raises:
            AELError(TEMPLATE_ERROR) if template is invalid or
            references undefined variables
        """
        templates_found: list[str] = []

        def render_value(value: Any) -> Any:
            """Recursively render a value."""
            if isinstance(value, str):
                if not has_templates(value):
                    return value

                # Track templates
                templates = extract_templates(value)
                templates_found.extend(templates)

                # Validate syntax
                errors = validate_syntax(value)
                if errors:
                    raise create_error(
                        "TEMPLATE_ERROR",
                        detail="; ".join(errors),
                    )

                # Render
                return self.render_string(value, context)

            elif isinstance(value, dict):
                return {k: render_value(v) for k, v in value.items()}

            elif isinstance(value, list):
                return [render_value(item) for item in value]

            else:
                # Primitive types - return as-is
                return value

        rendered = render_value(template)
        return RenderResult(
            value=rendered,
            had_templates=len(templates_found) > 0,
            templates_rendered=templates_found,
        )

    def render_string(
        self,
        template_str: str,
        context: TemplateContext,
    ) -> Any:
        """Render a single template string.

        If string is entirely a template (e.g., "{{ inputs.count }}"),
        returns the actual type (int, list, etc.).

        If string contains mixed content (e.g., "Hello {{ name }}"),
        returns string.

        Args:
            template_str: String with {{ }} templates
            context: Template context

        Returns:
            Rendered value (type preserved for pure templates)

        Raises:
            AELError(TEMPLATE_ERROR) on rendering errors
        """
        # Check if pure template (type preservation)
        if is_pure_template(template_str):
            templates = extract_templates(template_str)
            if templates:
                return self._evaluate_expression(templates[0], context)

        # Mixed content - string interpolation
        result = template_str
        templates = extract_templates(template_str)

        for template in templates:
            value = self._evaluate_expression(template, context)
            # Convert to string for interpolation
            str_value = str(value) if value is not None else ""
            result = result.replace("{{ " + template + " }}", str_value)
            result = result.replace("{{" + template + "}}", str_value)

        return result

    def render_params(
        self,
        params: dict[str, Any],
        context: TemplateContext,
    ) -> dict[str, Any]:
        """Render all templates in a params dict.

        Recursively renders templates in nested dicts and lists.

        Args:
            params: Parameters dict
            context: Template context

        Returns:
            Rendered params dict
        """
        result = self.render(params, context)
        return result.value  # type: ignore

    def validate(self, template: Any) -> list[str]:
        """Validate template syntax without rendering.

        Returns list of errors (empty if valid).
        Does NOT check variable existence.

        Args:
            template: Value to validate

        Returns:
            List of error messages (empty if valid)
        """
        errors: list[str] = []

        def validate_value(value: Any) -> None:
            """Recursively validate a value."""
            if isinstance(value, str):
                errors.extend(validate_syntax(value))
            elif isinstance(value, dict):
                for v in value.values():
                    validate_value(v)
            elif isinstance(value, list):
                for item in value:
                    validate_value(item)

        validate_value(template)
        return errors

    def extract_references(self, template: Any) -> list[str]:
        """Extract all variable references from template.

        E.g., "{{ inputs.url }}" → ["inputs.url"]

        Useful for dependency analysis.

        Args:
            template: Value to extract from

        Returns:
            List of variable references
        """
        return extract_all_references(template)

    def _evaluate_expression(self, expression: str, context: TemplateContext) -> Any:
        """Evaluate a template expression.

        Args:
            expression: Expression to evaluate (without {{ }})
            context: Template context

        Returns:
            Evaluated value

        Raises:
            AELError(TEMPLATE_ERROR) on evaluation errors
        """
        # Split into variable and filters
        parts = [p.strip() for p in expression.split("|")]
        var_path = parts[0]
        filters = parts[1:] if len(parts) > 1 else []

        # Resolve variable
        try:
            value = self._resolve_variable(var_path, context)
        except (KeyError, AttributeError, IndexError) as e:
            raise create_error(
                "TEMPLATE_ERROR",
                variable=var_path,
            ) from e

        # Apply filters
        for filter_expr in filters:
            value = self._apply_filter(filter_expr, value)

        return value

    def _resolve_variable(self, path: str, context: TemplateContext) -> Any:
        """Resolve a variable path like 'inputs.url' or 'steps.fetch.output'.

        Args:
            path: Dot-separated path
            context: Template context

        Returns:
            Resolved value

        Raises:
            KeyError, AttributeError, IndexError if path is invalid
        """
        parts = path.split(".")
        if not parts:
            raise KeyError(f"Empty variable path: {path}")

        # Start with root namespace
        root = parts[0]
        if root == "inputs":
            current = context.inputs
        elif root == "steps":
            current = context.steps
        elif root == "config":
            current = context.config
        elif root == "execution_id":
            return context.execution_id
        else:
            raise KeyError(f"Unknown namespace: {root}")

        # Navigate path
        for part in parts[1:]:
            # Handle array indexing
            if "[" in part and "]" in part:
                key = part[: part.index("[")]
                index_str = part[part.index("[") + 1 : part.index("]")]
                try:
                    index = int(index_str)
                except ValueError as e:
                    raise KeyError(f"Invalid array index: {index_str}") from e

                if key:
                    current = current[key] if isinstance(current, dict) else getattr(current, key)
                # Index into list/tuple
                if isinstance(current, (list, tuple)):
                    current = current[index]
                else:
                    raise TypeError(f"Cannot index {type(current)} with integer")
            else:
                # Regular attribute/key access
                current = current[part] if isinstance(current, dict) else getattr(current, part)

        return current

    def _apply_filter(self, filter_expr: str, value: Any) -> Any:
        """Apply a filter to a value.

        Args:
            filter_expr: Filter expression (e.g., "default(0)" or "length")
            value: Value to filter

        Returns:
            Filtered value

        Raises:
            AELError(TEMPLATE_ERROR) if filter is unknown
        """
        # Parse filter name and args
        if "(" in filter_expr:
            filter_name = filter_expr[: filter_expr.index("(")].strip()
            args_str = filter_expr[filter_expr.index("(") + 1 : filter_expr.rindex(")")].strip()
            # Simple arg parsing (just handle single values for now)
            args = [self._parse_filter_arg(args_str)] if args_str else []
        else:
            filter_name = filter_expr.strip()
            args = []

        # Get filter function
        if filter_name not in self._filters:
            raise create_error(
                "TEMPLATE_ERROR",
                filter_name=filter_name,
                supported_filters=", ".join(self._filters.keys()),
            )

        filter_func = self._filters[filter_name]
        return filter_func(value, *args)

    def _parse_filter_arg(self, arg_str: str) -> Any:
        """Parse a filter argument.

        Args:
            arg_str: Argument string

        Returns:
            Parsed value (str, int, float, or bool)
        """
        arg_str = arg_str.strip()

        # String literal
        if (arg_str.startswith("'") and arg_str.endswith("'")) or (
            arg_str.startswith('"') and arg_str.endswith('"')
        ):
            return arg_str[1:-1]

        # Boolean
        if arg_str == "true":
            return True
        if arg_str == "false":
            return False

        # Number
        try:
            if "." in arg_str:
                return float(arg_str)
            return int(arg_str)
        except ValueError:
            # Return as string if can't parse
            return arg_str
