export type TranscriptInsertion = { value: string; caret: number }

export function insertTranscriptAtSelection(
  value: string,
  selectionStart: number,
  selectionEnd: number,
  transcript: string,
): TranscriptInsertion

export function speechAvailabilityMessage(
  availability: 'unavailable' | 'downloadable' | 'downloading',
  language: string,
): string

export function speechRecognitionErrorMessage(error: string): string
