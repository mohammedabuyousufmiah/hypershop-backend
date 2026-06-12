"""Domain errors for the product Q&A module."""

from __future__ import annotations

from app.core.errors import ConflictError, NotFoundError


class QuestionNotFoundError(NotFoundError):
    code = "qa_question_not_found"
    public_message = "Question not found."


class AnswerNotFoundError(NotFoundError):
    code = "qa_answer_not_found"
    public_message = "Answer not found."


class QuestionBadStateError(ConflictError):
    code = "qa_question_bad_state"
    public_message = "Question is not in a state that allows this action."


class AnswerBadStateError(ConflictError):
    code = "qa_answer_bad_state"
    public_message = "Answer is not in a state that allows this action."


class AnswerHelpfulSelfVoteError(ConflictError):
    code = "qa_answer_helpful_self_vote"
    public_message = "You cannot mark your own answer as helpful."


class QAEditWindowExpiredError(ConflictError):
    code = "qa_edit_window_expired"
    public_message = "The 24-hour edit window has elapsed."
