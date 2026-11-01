"""Typed exceptions raised by pipeline configuration and processing."""


class PipelineError(Exception):
    """Base exception for expected pipeline errors."""


class WorkbookReadError(PipelineError):
    """Raised when a workbook cannot be opened or read."""


class WorkbookValidationError(PipelineError):
    """Raised when workbook structure violates explicit reader settings."""


class MappingError(PipelineError):
    """Raised when a source row cannot be transformed by its field mapping."""


class ConfigurationError(PipelineError):
    """Raised when YAML or an in-memory pipeline configuration is invalid."""
