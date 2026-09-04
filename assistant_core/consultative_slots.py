"""Awareness de slots consultivos — evita repetir perguntas já respondidas semanticamente."""

from __future__ import annotations

import re
from dataclasses import dataclass

from assistant_core.conversation_turns import ENVIRONMENT_MARKERS, normalize_text

FLOOR_SURFACE_MARKERS = (
    "concreto",
    "porcelanato",
    "epoxi",
    "epóxi",
    "ceramica",
    "cerâmica",
    "granito",
    "marmore",
    "mármore",
    "vinil",
    "resina",
)
ENVIRONMENT_TYPE_MARKERS = (
    "galpao",
    "galpão",
    "armazem",
    "armazém",
    "deposito",
    "depósito",
    "shopping",
    "supermercado",
    "hospital",
    "industria",
    "indústria",
    "fabrica",
    "fábrica",
    "facilities",
    "corredor",
    "logistica",
    "logística",
)
AREA_MARKERS = ("m2", "m²", "metro quadrado", "metragem", "metros quadrados")
CIRCULATION_MARKERS = ("circulando", "fluxo", "pessoas", "transito", "trânsito", "movimento", "noite", "horario", "horário")

CLEANING_FOLLOWUP_ENVIRONMENT_AND_FLOOR = "Qual é o ambiente e o tipo de piso onde a limpeza acontece?"
CLEANING_FOLLOWUP_AREA = "Qual é a metragem aproximada da área?"
CLEANING_FOLLOWUP_FLOOR = "Qual é o tipo de piso nesse ambiente?"
CLEANING_FOLLOWUP_ENVIRONMENT_ONLY = "Qual é o tipo de ambiente onde a limpeza acontece?"
CLEANING_FOLLOWUP_CIRCULATION = (
    "O fluxo de pessoas nesse ambiente é constante ou há horários com menos circulação?"
)
CLEANING_FOLLOWUP_OPERATION = "Com que frequência vocês precisam de limpeza nesse espaço?"


@dataclass(frozen=True)
class ConsultativeSlots:
    environment_type: bool = False
    floor_surface: bool = False
    area_size: bool = False
    circulation: bool = False

    @property
    def environment_and_floor_known(self) -> bool:
        return self.environment_type and self.floor_surface


def _conversation_blob(*, need_summary: str = "", history=None, current_message: str = "") -> str:
    parts = [str(need_summary or "")]
    for item in history or []:
        if str(item.get("role") or "") == "user":
            parts.append(str(item.get("content") or ""))
    parts.append(str(current_message or ""))
    return normalize_text(" ".join(parts))


def extract_consultative_slots(
    *,
    need_summary: str = "",
    history=None,
    current_message: str = "",
) -> ConsultativeSlots:
    blob = _conversation_blob(
        need_summary=need_summary,
        history=history,
        current_message=current_message,
    )
    environment_type = any(marker in blob for marker in ENVIRONMENT_TYPE_MARKERS)
    if not environment_type:
        environment_type = any(marker in blob for marker in ENVIRONMENT_MARKERS if marker in ENVIRONMENT_TYPE_MARKERS)
    floor_surface = any(marker in blob for marker in FLOOR_SURFACE_MARKERS)
    if not floor_surface and "piso" in blob:
        floor_surface = any(marker in blob for marker in FLOOR_SURFACE_MARKERS)
    area_size = bool(re.search(r"\b\d+\s*m\b", blob)) or any(marker in blob for marker in AREA_MARKERS)
    circulation = any(marker in blob for marker in CIRCULATION_MARKERS)
    return ConsultativeSlots(
        environment_type=environment_type,
        floor_surface=floor_surface,
        area_size=area_size,
        circulation=circulation,
    )


def is_cleaning_consultation(*, memory=None, need_summary: str = "", current_message: str = "") -> bool:
    blob = normalize_text(
        " ".join(
            [
                str(getattr(memory, "active_application", "") or ""),
                str(getattr(memory, "active_topic", "") or ""),
                str(getattr(memory, "active_domain", "") or ""),
                str(need_summary or ""),
                str(current_message or ""),
            ]
        )
    )
    if getattr(memory, "active_application", "") == "cleaning_robotics":
        return True
    if getattr(memory, "active_topic", "") == "cleaning_robot":
        return True
    return any(token in blob for token in ("limpeza", "hygibot", "duno", "dune", "lavar", "varrer", "aspirar"))


def select_cleaning_followup(
    *,
    slots: ConsultativeSlots,
    current_message: str = "",
) -> str:
    """Escolhe o próximo slot consultivo de limpeza ainda não respondido."""
    msg = normalize_text(current_message)
    just_answered_env = any(marker in msg for marker in ENVIRONMENT_TYPE_MARKERS)
    just_answered_floor = any(marker in msg for marker in FLOOR_SURFACE_MARKERS)
    just_answered_area = bool(re.search(r"\b\d+\s*m\b", msg)) or any(marker in msg for marker in AREA_MARKERS)

    if slots.environment_and_floor_known:
        if not slots.area_size and not just_answered_area:
            return CLEANING_FOLLOWUP_AREA
        if not slots.circulation:
            return CLEANING_FOLLOWUP_CIRCULATION
        return CLEANING_FOLLOWUP_OPERATION

    if slots.environment_type and not slots.floor_surface:
        if just_answered_env and not just_answered_floor:
            return CLEANING_FOLLOWUP_FLOOR
        return CLEANING_FOLLOWUP_FLOOR

    if slots.floor_surface and not slots.environment_type:
        return CLEANING_FOLLOWUP_ENVIRONMENT_ONLY

    if just_answered_env or just_answered_floor or just_answered_area:
        if not slots.area_size:
            return CLEANING_FOLLOWUP_AREA
        if not slots.floor_surface:
            return CLEANING_FOLLOWUP_FLOOR
        if not slots.environment_type:
            return CLEANING_FOLLOWUP_ENVIRONMENT_ONLY

    return CLEANING_FOLLOWUP_ENVIRONMENT_AND_FLOOR


def environment_floor_followup_already_answered(
    *,
    need_summary: str = "",
    history=None,
    current_message: str = "",
) -> bool:
    slots = extract_consultative_slots(
        need_summary=need_summary,
        history=history,
        current_message=current_message,
    )
    return slots.environment_and_floor_known


def followup_requests_attribute(followup: str, attribute_markers: tuple[str, ...]) -> bool:
    normalized = normalize_text(followup)
    return any(marker in normalized for marker in attribute_markers)


def should_skip_followup_for_answered_slots(
    followup: str,
    *,
    need_summary: str = "",
    history=None,
    current_message: str = "",
) -> bool:
    """True quando o follow-up repete atributo já presente no contexto acumulado."""
    if not followup:
        return True
    slots = extract_consultative_slots(
        need_summary=need_summary,
        history=history,
        current_message=current_message,
    )
    normalized = normalize_text(followup)
    asks_environment = any(token in normalized for token in ("ambiente", "galp", "local"))
    asks_floor = any(token in normalized for token in ("piso", "concreto", "porcelanato", "epoxi"))
    asks_area = any(token in normalized for token in ("metragem", "m2", "m²", "metro quadrado"))
    if asks_environment and asks_floor and slots.environment_and_floor_known:
        return True
    if asks_environment and slots.environment_type and not asks_floor:
        return True
    if asks_floor and slots.floor_surface:
        return True
    if asks_area and slots.area_size:
        return True
    return False
