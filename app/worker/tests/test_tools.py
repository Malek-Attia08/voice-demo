"""The tools, in plain Python (no LiveKit, no Gemini).

Run from C:\\voice-demo\\app:
    python -m unittest discover -s worker/tests -t .
"""
import unittest

from worker import tools


class SchemaTests(unittest.TestCase):
    def test_the_five_tools_are_declared_with_a_json_schema(self):
        names = [tool["name"] for tool in tools.TOOLS]
        self.assertEqual(names, ["rag_answer", "history", "medical_info", "get_time", "find_appointment_slots"])
        for tool in tools.TOOLS:
            self.assertTrue(tool["description"])
            self.assertEqual(tool["parameters"]["type"], "object")

    def test_rag_answer_requires_a_question(self):
        rag = next(tool for tool in tools.TOOLS if tool["name"] == "rag_answer")
        self.assertEqual(rag["parameters"]["required"], ["question"])


class RagAnswerTests(unittest.TestCase):
    def test_a_known_topic_gets_its_canned_snippet(self):
        answer = tools.call("rag_answer", {"question": "How do I cancel an appointment?"})
        self.assertIn("24 hours", answer)

    def test_the_match_ignores_case(self):
        self.assertEqual(tools.call("rag_answer", {"question": "PRESCRIPTION?"}),
                         tools.call("rag_answer", {"question": "prescription?"}))

    def test_an_unknown_topic_says_nothing_was_found(self):
        self.assertIn("nothing", tools.call("rag_answer", {"question": "quantum sandwiches"}).lower())

    def test_the_assistants_own_role_is_answered(self):
        for question in ("What is your role?", "Who are you?", "Quel est votre rôle ?", "Wat kunt u voor mij doen? wat kunt u"):
            self.assertNotIn("nothing", tools.call("rag_answer", {"question": question}).lower(), question)
        self.assertIn("not a doctor", tools.call("rag_answer", {"question": "what can you do"}))

    def test_questions_in_french_dutch_and_german_are_understood(self):
        for question in ("Comment annuler un rendez-vous ?", "Een afspraak annuleren", "Ich möchte einen Termin absagen"):
            self.assertIn("24 hours", tools.call("rag_answer", {"question": question}), question)
        self.assertIn("8:00", tools.call("rag_answer", {"question": "Quels sont vos horaires d'ouverture ?"}))
        self.assertIn("prescriptions", tools.call("rag_answer", {"question": "Je veux renouveler mon ordonnance"}).lower())

    def test_the_same_question_always_gets_the_same_answer(self):
        question = {"question": "opening hours"}
        self.assertEqual(tools.call("rag_answer", question), tools.call("rag_answer", question))


class HistoryTests(unittest.TestCase):
    def test_it_returns_the_last_turns_oldest_first(self):
        turns = [("user", "hi"), ("agent", "hello"), ("user", "appointment?"), ("agent", "24 hours")]
        answer = tools.call("history", {"count": 2}, turns=turns)
        self.assertEqual(answer, "user: appointment?\nagent: 24 hours")

    def test_it_defaults_to_the_last_five(self):
        turns = [("user", str(n)) for n in range(8)]
        self.assertEqual(len(tools.call("history", {}, turns=turns).splitlines()), 5)

    def test_an_empty_call_says_so(self):
        self.assertIn("no earlier", tools.call("history", {}, turns=[]).lower())


class MedicalInfoTests(unittest.TestCase):
    def test_it_requires_a_topic(self):
        medical = next(tool for tool in tools.TOOLS if tool["name"] == "medical_info")
        self.assertEqual(medical["parameters"]["required"], ["topic"])

    def test_a_known_topic_gets_its_canned_general_information(self):
        answer = tools.call("medical_info", {"topic": "I have a fever"})
        self.assertIn("fever", answer.lower())
        self.assertIn("not medical advice", answer)

    def test_the_match_ignores_case(self):
        self.assertEqual(tools.call("medical_info", {"topic": "HEADACHE"}), tools.call("medical_info", {"topic": "headache"}))

    def test_an_unknown_topic_says_nothing_was_found_and_points_to_a_professional(self):
        answer = tools.call("medical_info", {"topic": "quantum sandwiches"})
        self.assertIn("nothing", answer.lower())
        self.assertIn("doctor or pharmacist", answer)

    def test_a_possible_emergency_always_points_to_emergency_services_first(self):
        for topic in ("sudden chest pain", "he is unconscious", "I cannot breathe, also a headache"):
            answer = tools.call("medical_info", {"topic": topic})
            self.assertTrue(answer.startswith("This could be an emergency"), topic)
            self.assertIn("112", answer)

    def test_it_never_diagnoses(self):
        self.assertNotIn("you have", tools.call("medical_info", {"topic": "fever"}).lower())


class SmallToolsTests(unittest.TestCase):
    def test_get_time_uses_the_clock_it_is_given(self):
        self.assertEqual(tools.call("get_time", {}, now=lambda: "2026-11-14 10:30"), "It is 2026-11-14 10:30.")

    def test_appointment_slots_are_listed_by_specialty(self):
        self.assertIn("Monday 9:30", tools.call("find_appointment_slots", {"specialty": "general practice"}))
        self.assertIn("Wednesday 10:00", tools.call("find_appointment_slots", {"specialty": "pédiatrie"}))
        self.assertIn("Monday 9:30", tools.call("find_appointment_slots", {}))

    def test_an_unknown_specialty_says_nothing_is_listed_and_nothing_is_booked(self):
        answer = tools.call("find_appointment_slots", {"specialty": "astrology"})
        self.assertIn("no slots", answer)
        self.assertNotIn("book", answer.lower())


class UnknownToolTests(unittest.TestCase):
    def test_an_unknown_tool_is_reported_not_raised(self):
        self.assertIn("unknown tool", tools.call("launch_rocket", {}).lower())


if __name__ == "__main__":
    unittest.main()
