from __future__ import annotations


ENTITY_TYPES = {
    "company",
    "concept",
    "evidence_source_group",
    "industry",
    "industry_chain_node",
    "product",
    "security",
    "theme",
}


PREDICATES = {
    "alias_of",
    "belongs_to",
    "has_component",
    "has_product_exposure",
    "maps_to_concept",
    "preferred_source_for",
    "supplies_to",
}


PREDICATE_RELATIONS = {
    "alias_of": {
        "subject_types": sorted(ENTITY_TYPES),
        "object_types": sorted(ENTITY_TYPES),
        "description": "实体别名或同义关系，应优先用于实体消歧。",
    },
    "belongs_to": {
        "subject_types": ["company", "industry_chain_node", "product", "security"],
        "object_types": ["concept", "industry", "theme"],
        "description": "实体属于行业、概念或主题。",
    },
    "has_component": {
        "subject_types": ["concept", "industry", "industry_chain_node", "theme"],
        "object_types": ["industry_chain_node", "product"],
        "description": "主题、行业或产业链节点包含下级节点或产品。",
    },
    "has_product_exposure": {
        "subject_types": ["company", "security"],
        "object_types": ["industry_chain_node", "product", "theme"],
        "description": "公司或证券对某产品、节点或主题有业务暴露。",
    },
    "maps_to_concept": {
        "subject_types": ["company", "industry", "industry_chain_node", "product", "security", "theme"],
        "object_types": ["concept"],
        "description": "实体映射到概念板块或题材概念。",
    },
    "preferred_source_for": {
        "subject_types": ["evidence_source_group"],
        "object_types": ["concept", "company", "industry", "industry_chain_node", "product", "theme"],
        "description": "证据源组适合验证某类实体或主题。",
    },
    "supplies_to": {
        "subject_types": ["company", "product"],
        "object_types": ["company", "industry_chain_node", "product"],
        "description": "供应、客户或上下游关系。",
    },
}


def is_known_entity_type(value: str) -> bool:
    return value in ENTITY_TYPES


def is_known_predicate(value: str) -> bool:
    return value in PREDICATES


def relation_errors(predicate: str, subject_type: str, object_type: str) -> list[str]:
    if predicate not in PREDICATE_RELATIONS:
        return []
    spec = PREDICATE_RELATIONS[predicate]
    errors: list[str] = []
    if subject_type not in spec["subject_types"]:
        errors.append(f"subject type {subject_type!r} is not allowed for predicate {predicate!r}")
    if object_type not in spec["object_types"]:
        errors.append(f"object type {object_type!r} is not allowed for predicate {predicate!r}")
    return errors


def taxonomy_payload() -> dict[str, object]:
    return {
        "schema": "ashare.relation_taxonomy.v1",
        "entity_types": sorted(ENTITY_TYPES),
        "predicates": [
            {
                "predicate": predicate,
                "subject_types": list(PREDICATE_RELATIONS[predicate]["subject_types"]),
                "object_types": list(PREDICATE_RELATIONS[predicate]["object_types"]),
                "description": PREDICATE_RELATIONS[predicate]["description"],
            }
            for predicate in sorted(PREDICATES)
        ],
    }
