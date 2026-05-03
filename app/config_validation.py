import os
import sys
from typing import Optional
from pydantic import BaseModel, Field, ValidationError, field_validator

class Config(BaseModel):
    # Core
    ENV: str = Field("development", pattern=r"^(development|production|staging|test|local|dev)$")
    DATABASE_URL: str
    DATABASE_URL_SYNC: str
    
    # Auth (Supabase)
    SUPABASE_URL: str
    SUPABASE_ANON_KEY: str
    SUPABASE_JWT_SECRET: Optional[str] = None
    SUPABASE_SERVICE_ROLE_KEY: Optional[str] = None
    
    # Task Queue (Celery/RabbitMQ/Redis)
    RABBITMQ_URL: str
    UPSTASH_HOST: Optional[str] = None
    UPSTASH_PASSWORD: Optional[str] = None
    UPSTASH_PORT: str = "6379"
    
    # AI Services
    GROQ_API_KEY: str
    VOYAGE_API_KEY: str
    COHERE_API_KEY: Optional[str] = None
    QDRANT_URL: str
    QDRANT_API_KEY: str
    
    # Security
    CORS_ALLOWED_ORIGINS: str = "https://legalrag.codes,https://www.legalrag.codes,https://legal-ai-copilot-xi.vercel.app"
    FRONTEND_URL: Optional[str] = None
    CLOUDFLARE_ACCOUNT_ID: Optional[str] = None
    CLOUDFLARE_R2_ACCESS_KEY_ID: Optional[str] = None
    CLOUDFLARE_R2_SECRET_ACCESS_KEY: Optional[str] = None
    CLOUDFLARE_R2_BUCKET_NAME: Optional[str] = None
    
    @field_validator("FRONTEND_URL")
    @classmethod
    def validate_frontend_url(cls, v, info):
        if info.data.get("ENV") == "production" and not v:
            raise ValueError("FRONTEND_URL is required in production environment.")
        return v

def validate_config():
    """
    Validates all environment variables.
    Exits with error code 1 if validation fails in production.
    """
    try:
        # Load from os.environ
        config_data = {
            key: os.getenv(key) for key in Config.model_fields.keys() if os.getenv(key) is not None
        }
        # Add defaults for optional fields if they are missing
        if "ENV" not in config_data:
            config_data["ENV"] = os.getenv("ENV", "development")
            
        validated = Config(**config_data)
        if validated.ENV == "production":
            required_r2 = [
                "CLOUDFLARE_ACCOUNT_ID",
                "CLOUDFLARE_R2_ACCESS_KEY_ID",
                "CLOUDFLARE_R2_SECRET_ACCESS_KEY",
                "CLOUDFLARE_R2_BUCKET_NAME",
            ]
            missing_r2 = [k for k in required_r2 if not getattr(validated, k)]
            if missing_r2:
                raise ValueError(
                    "Missing required production R2 config: " + ", ".join(missing_r2)
                )
        print("✅ Environment configuration validated.")
        return validated
    except ValidationError as e:
        print("❌ Environment validation failed:")
        for error in e.errors():
            print(f"  - {'.'.join(str(loc) for loc in error['loc'])}: {error['msg']}")
        
        if os.getenv("ENV", "development").lower() == "production":
            print("FATAL: Project cannot start in production with invalid config.")
            sys.exit(1)
        else:
            print("WARNING: Missing environment variables. App may fail at runtime.")
            return None
    except ValueError as e:
        print("❌ Environment validation failed:")
        print(f"  - {e}")
        if os.getenv("ENV", "development").lower() == "production":
            print("FATAL: Project cannot start in production with invalid config.")
            sys.exit(1)
        print("WARNING: Missing environment variables. App may fail at runtime.")
        return None
