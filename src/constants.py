ENV_PREFIX = "MOLT_SERVER_"

MOLT_VERSION = "0.1.0"
USER_AGENT = f"molt/{MOLT_VERSION}"

OSV_API_URL = "https://api.osv.dev/v1/query"
PYPI_API_URL = "https://pypi.org/pypi"
GITHUB_API_URL = "https://api.github.com"

# Politeness cap on concurrent outbound requests during the enrichment fan-out.
MAX_CONCURRENT_REQUESTS = 10
HTTP_TIMEOUT_SECONDS = 20.0

CACHE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_CACHE_PATH = "~/.molt/molt.db"

# Default model for every agentic tool; override per tool in the env file with
# MOLT_SERVER_<TOOL>_LLM_MODEL, or for all tools with MOLT_SERVER_DEFAULT_LLM_MODEL.
DEFAULT_LLM_MODEL = "anthropic:claude-opus-5"

ASSESS_SECURITY = "assess_security"
ASSESS_LICENSE = "assess_license"
ASSESS_MAINTENANCE = "assess_maintenance"
TRIAGE_DEPENDENCIES = "triage_dependencies"
FIND_REPLACEMENT = "find_replacement"
MAX_CONCURRENT_LLM_CALLS = 5

DEPS_DEV_API_URL = "https://api.deps.dev/v3alpha"

# Maintenance thresholds, in days. Quiet for eighteen months is a signal; quiet for
# three years with no release is the end of the road. Both are conventions, which is
# exactly why they live here as named numbers rather than inside a prompt.
DECLINING_AFTER_DAYS = 548
ABANDONED_AFTER_DAYS = 1095

# The ReAct loop in find_replacement reasons, looks something up, and reasons again.
# Five turns is enough for "identify the domain, propose candidates, verify them";
# beyond that the loop is usually circling rather than converging.
MAX_REACT_ITERATIONS = 5

# Semantic-memory key for a replacement this project has validated. It lives here
# rather than in either tool because find_replacement reads it and record_decision
# writes it, and a key only one of them knows is a key that drifts.
REPLACEMENT_KEY = "replacement:{package}"

DEFAULT_MEMORY_PATH = "~/.molt/molt.db"
BACKUP_SUFFIX = ".molt.bak"
