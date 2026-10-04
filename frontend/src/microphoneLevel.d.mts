export type LocalMicrophoneAnalyzer = {
  analyser: AnalyserNode
  dispose: () => Promise<void>
}

export function rmsFromTimeDomainData(samples: ArrayLike<number>): number
export function normalizeRms(rms: number): number
export function openLocalMicrophoneAnalyzer(environment?: {
  mediaDevices?: Pick<MediaDevices, 'getUserMedia'>
  AudioContext?: typeof AudioContext
}): Promise<LocalMicrophoneAnalyzer>
