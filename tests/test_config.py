import pytest

from gateway.config import load_config


def test_production_configuration_fails_closed_without_a_live_adapter():
    with pytest.raises(ValueError, match="production payment adapter"):
        load_config({"GATEWAY_ENVIRONMENT": "production"})


def test_load_config_keeps_result_access_key_secret_and_validates_clawtip_resource_url():
    config = load_config(
        {
            "RESULT_ACCESS_TOKEN_KEY": "persistent-secret-" + "x" * 32,
            "CLAWTIP_RESOURCE_URL": "https://gateway.example/v1/results",
        }
    )

    assert config.result_access_token_key.get_secret_value().startswith("persistent-secret-")
    assert config.result_access_token_key.get_secret_value() not in repr(config)
    with pytest.raises(ValueError, match="absolute HTTPS"):
        load_config({"CLAWTIP_RESOURCE_URL": "http://gateway.example/result"})
