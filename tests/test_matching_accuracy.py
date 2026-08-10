from app.matching import evaluate_relevance


def test_broad_synonym_alone_does_not_trigger():
    result = evaluate_relevance("锅炉检修技术服务项目", "", ["信息化"], [])
    assert result.score < 20


def test_business_object_and_action_combination_triggers():
    result = evaluate_relevance("生产管理信息系统升级改造服务", "", ["信息化"], [])
    assert result.score >= 20
    assert any("业务组合" in reason for reason in result.reasons)


def test_product_only_purchase_is_downranked():
    result = evaluate_relevance("办公软件采购项目", "", ["软件开发"], [])
    assert result.score < 20
