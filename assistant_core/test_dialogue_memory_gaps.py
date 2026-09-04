from django.test import SimpleTestCase

from assistant_core.consultative_policy import build_conceptual_price_reply, is_conceptual_price_question
from assistant_core.dialogue_memory import (
    DialogueMemory,
    build_contextual_retrieval_query,
    infer_domain,
    update_dialogue_memory_from_turn,
)


class DialogueMemoryGapCuradoriaTests(SimpleTestCase):
    def test_site_marker_infers_software_web(self):
        self.assertEqual(infer_domain("preciso de um site"), "software_web")
        self.assertEqual(infer_domain("vocês fazem loja virtual?"), "software_web")

    def test_domain_switch_clears_robot_entity_for_site(self):
        memory = DialogueMemory(active_entity="Duno", active_domain="robotics", active_topic="cleaning_robot")
        update_dialogue_memory_from_turn(memory=memory, current_message="preciso de um site")
        self.assertEqual(memory.active_domain, "software_web")
        self.assertEqual(memory.active_entity, "")
        self.assertEqual(memory.active_topic, "websites")

    def test_lineup_question_clears_sticky_entity(self):
        memory = DialogueMemory(active_entity="Duno", active_domain="robotics", active_topic="cleaning_robot")
        update_dialogue_memory_from_turn(memory=memory, current_message="quais robôs vocês têm?")
        self.assertEqual(memory.active_entity, "")
        self.assertEqual(memory.active_topic, "robot_lineup")
        _, contextual = build_contextual_retrieval_query(current_message="quais robôs vocês têm?", memory=memory)
        self.assertIn("Xyron", contextual)
        self.assertNotIn("HygiBot Dune limpeza", contextual)

    def test_pitondo_topic_switch_kitchen_to_stairs(self):
        memory = DialogueMemory(active_domain="materials", active_topic="kitchen", active_need="bancada cozinha")
        update_dialogue_memory_from_turn(memory=memory, current_message="e para escadas?")
        self.assertEqual(memory.active_topic, "stairs")
        _, contextual = build_contextual_retrieval_query(current_message="e para escadas?", memory=memory)
        self.assertIn("escadas", contextual.lower())

    def test_school_domain_stays_robotics_with_educational_topic(self):
        memory = DialogueMemory()
        update_dialogue_memory_from_turn(memory=memory, current_message="tem robô para escola?")
        self.assertEqual(memory.active_domain, "robotics")
        self.assertEqual(memory.active_topic, "educational_robot")

    def test_bncc_keeps_liro_context_educational(self):
        memory = DialogueMemory(active_entity="LIRO", active_domain="robotics", active_topic="educational_robot")
        update_dialogue_memory_from_turn(memory=memory, current_message="ele atende a BNCC robótica?")
        self.assertEqual(memory.active_entity, "LIRO")
        self.assertEqual(memory.active_domain, "robotics")
        self.assertEqual(memory.active_topic, "educational_robot")
        self.assertEqual(memory.active_application, "educational_robotics")
        _, contextual = build_contextual_retrieval_query(current_message="ele atende a BNCC robótica?", memory=memory)
        self.assertIn("LIRO", contextual)
        self.assertIn("robótica educacional", contextual)
        self.assertNotIn("robô de limpeza", contextual)

    def test_duno_price_is_policy_not_invention(self):
        self.assertTrue(is_conceptual_price_question("quanto custa o Duno?"))
        reply = build_conceptual_price_reply(current_message="quanto custa o Duno?")
        self.assertIn("investimento", reply.lower())
        self.assertIn("duno", reply.lower())
        self.assertNotRegex(reply, r"R\$\s*\d")
        self.assertIn("orçamento", reply.lower())

    def test_quote_process_question_is_not_collection(self):
        from assistant_core.consultative_policy import detect_collection_trigger, CollectionTrigger

        self.assertEqual(
            detect_collection_trigger("preciso mandar medida e foto para orçamento?"),
            CollectionTrigger.NONE,
        )
        self.assertEqual(
            detect_collection_trigger("quero um orçamento para a bancada"),
            CollectionTrigger.BUDGET,
        )
