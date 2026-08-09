import pytest

from gateway.catalog import PRODUCTS, get_product
from scripts.validate_repo import main, validate_product_skills


def test_catalog_has_exactly_three_products():
    assert set(PRODUCTS) == {"outcome-offer", "proof-pack", "reply-to-close"}


def test_each_product_has_fixed_dual_price():
    for product in PRODUCTS.values():
        assert product.prices["USD"].minor_units == 1
        assert product.prices["CNY"].minor_units == 6


def test_unknown_product_is_rejected():
    try:
        get_product("unknown")
    except KeyError as error:
        assert error.args == ("unknown",)
    else:
        raise AssertionError("unknown product was accepted")


def test_catalog_and_product_prices_are_immutable():
    product = get_product("outcome-offer")

    with pytest.raises(TypeError):
        PRODUCTS["other"] = product

    with pytest.raises(TypeError):
        product.prices["USD"] = product.prices["USD"]


def test_product_skill_validator_requires_the_exact_skill_set(tmp_path):
    skills = tmp_path / "skills"
    for skill_id in ("outcome-offer", "proof-pack", "reply-to-close"):
        skill_dir = skills / skill_id
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: test\n---\n", encoding="utf-8")

    validate_product_skills(tmp_path)

    extra_skill_dir = skills / "extra"
    extra_skill_dir.mkdir()
    (extra_skill_dir / "SKILL.md").write_text("---\nname: test\n---\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected product Skills"):
        validate_product_skills(tmp_path)


def test_validator_reports_non_object_manifest(tmp_path, capsys):
    manifest_dir = tmp_path / ".codex-plugin"
    manifest_dir.mkdir()
    (manifest_dir / "plugin.json").write_text("[]", encoding="utf-8")

    assert main([str(tmp_path)]) == 1
    assert "plugin manifest must contain a JSON object" in capsys.readouterr().err
