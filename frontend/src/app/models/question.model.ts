/**
 * Question / QuestionPack types — Phase 4 (Question Tool).
 *
 * Mirrors the backend Pydantic models in `daemon/models.py` (Question +
 * QuestionPack). The SSE ``question_pack`` event carries the same shape in
 * the ``message`` field; ``POST /api/instances/{id}/answer`` echoes the
 * updated pack (status='answered') back as its response body.
 */
export interface Question {
  id: string;
  text: string;
  options?: string[];
  allow_custom: boolean;
  required: boolean;
  answer?: string;
}

export interface QuestionPack {
  instance_id: string;
  questions: Question[];
  status: 'pending' | 'answered';
  answers: Record<string, string>;
  created_at: string;
}
