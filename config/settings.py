import os

from dotenv import load_dotenv

load_dotenv()

# OpenRouter configuration. The API key must come from the environment / .env,
# never from source code.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_TIMEOUT = 90

# Temperature settings
CREATIVE_TEMP = 0.7    # For conversational tone & tailoring
ANALYTICAL_TEMP = 0.2  # For precise parsing

# ---- Deployment mode -------------------------------------------------------
# local: one user, no sign-in, SQLite, background worker runs inside the app.
# oidc:  hosted SaaS. Users sign in with st.login, plan limits apply, worker runs separately.
AUTH_MODE = os.getenv("AUTH_MODE", "local")
HOSTED = AUTH_MODE == "oidc"
APP_ENV = os.getenv("APP_ENV", "production" if HOSTED else "development")
EMBEDDED_WORKER = os.getenv("EMBEDDED_WORKER", "0" if HOSTED else "1") == "1"

# ---- Storage ----------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DATA_DIR, "careerready.sqlite3")
DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{DB_PATH}"  # e.g. postgresql+psycopg://user:pw@host/db
# Comma-separated Fernet keys (newest first) used to encrypt resumes and letters at rest.
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")

# ---- Billing (Stripe) -------------------------------------------------------
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PAYMENT_LINK = os.getenv("STRIPE_PAYMENT_LINK", "")   # Payment Link for the Pro plan
STRIPE_PORTAL_URL = os.getenv("STRIPE_PORTAL_URL", "")       # Customer portal login link

# ---- Worker -----------------------------------------------------------------
TASK_MAX_ATTEMPTS = 3
TASK_STALE_SECONDS = 900   # a "running" task older than this is assumed to belong to a dead worker

# Sites that forbid automated applications or sit behind CAPTCHAs / logins.
# The apply agent refuses these and tells the user to apply by hand.
BLOCKED_APPLY_DOMAINS = (
    "linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "monster.com",
    "workday.com", "myworkdayjobs.com",
)
