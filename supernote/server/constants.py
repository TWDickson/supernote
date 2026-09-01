"""Server constants."""

# System directories that cannot be deleted or renamed, by the place they
# occupy rather than by the word they are spelled with. The firmware expects
# these to exist at these locations, which is what makes them immutable; a
# folder the user happened to name "Document" inside their own notebook folder
# is not one of them, and is theirs to delete.
#
# Firmware creates EXPORT, INBOX and SCREENSHOT where the bootstrap below
# creates Export, Inbox and Screenshot. Both spellings name the same place, so
# these three are matched without regard to case.
CASE_VARIANT_SYSTEM_ROOTS = {
    "Export",
    "Inbox",
    "Screenshot",
}

# The category containers are emphatically *not* in that set. Case is load
# bearing here: `NOTE` is the container and `Note` is the folder inside it,
# `DOCUMENT` likewise. Folding these together resolves a path to the wrong one
# of two real directories, so they are matched exactly.
SYSTEM_ROOT_DIRECTORIES = CASE_VARIANT_SYSTEM_ROOTS | {"NOTE", "DOCUMENT"}

# Deprecated: the flat name-anywhere set this replaced. Kept as the union of
# the two location-scoped sets so that callers reading it for display or for
# test filtering keep working; it must not be used to decide immutability,
# because a bare name cannot answer that question.
IMMUTABLE_SYSTEM_DIRECTORIES = SYSTEM_ROOT_DIRECTORIES | {
    "Note",
    "Document",
    "MyStyle",
}

# Category containers (hidden from web API)
CATEGORY_CONTAINERS = {"NOTE", "DOCUMENT"}

# Explicit mapping of system category subfolders to their parent container
SYSTEM_CATEGORY_CONTAINER_MAP = {
    "Note": "NOTE",
    "MyStyle": "NOTE",
    "Document": "DOCUMENT",
}

# Forced order and specific names for web API root (when flatten=True)
ORDERED_WEB_ROOT = ["Note", "Document"]

# Blob Storage Buckets
USER_DATA_BUCKET = "supernote-user-data"
CACHE_BUCKET = "supernote-cache"

# Maximum upload size for file uploads
MAX_UPLOAD_SIZE = 1024 * 1024 * 1024  # 1GB

# Database connection settings
SQLITE_TIMEOUT_SECONDS = 60.0

# Task processing queue settings
DEFAULT_PAGE_CONCURRENCY = 4

# Task status database write retry settings
DB_WRITE_MAX_RETRIES = 5
DB_WRITE_RETRY_BACKOFF_SECONDS = 0.1
