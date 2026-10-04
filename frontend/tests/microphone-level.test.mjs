import assert from 'node:assert/strict'
import test from 'node:test'
import { normalizeRms, openLocalMicrophoneAnalyzer, rmsFromTimeDomainData } from '../src/microphoneLevel.mjs'

function samplesAt(amplitude) {
  return Uint8Array.from([128 - amplitude, 128 + amplitude, 128 - amplitude, 128 + amplitude])
}

test('microphone RMS and normalized level are bounded and increase with signal amplitude', () => {
  const silence = rmsFromTimeDomainData(new Uint8Array(16).fill(128))
  const quiet = rmsFromTimeDomainData(samplesAt(12))
  const loud = rmsFromTimeDomainData(samplesAt(48))

  assert.equal(silence, 0)
  assert.ok(quiet > silence)
  assert.ok(loud > quiet)
  assert.equal(normalizeRms(silence), 0)
  assert.ok(normalizeRms(quiet) < normalizeRms(loud))
  assert.equal(normalizeRms(Number.NaN), 0)
  assert.equal(normalizeRms(-1), 0)
  assert.equal(normalizeRms(10), 1)
  assert.equal(rmsFromTimeDomainData(new Uint8Array()), 0)
})

test('local microphone analyzer requests audio only and releases the stream and AudioContext', async () => {
  const calls = { constraints: null, connectedTo: null, trackStops: 0, disconnects: 0, resumed: 0, closed: 0 }
  const track = { stop: () => { calls.trackStops += 1 } }
  const analyser = { fftSize: 0, smoothingTimeConstant: 0, disconnect: () => { calls.disconnects += 1 } }
  const source = {
    connect: target => { calls.connectedTo = target },
    disconnect: () => { calls.disconnects += 1 },
  }
  const context = {
    state: 'suspended',
    createMediaStreamSource: () => source,
    createAnalyser: () => analyser,
    resume: async () => { calls.resumed += 1; context.state = 'running' },
    close: async () => { calls.closed += 1; context.state = 'closed' },
  }
  class FakeAudioContext {
    constructor() { return context }
  }
  const mediaDevices = {
    getUserMedia: async constraints => {
      calls.constraints = constraints
      return { getTracks: () => [track] }
    },
  }

  const monitor = await openLocalMicrophoneAnalyzer({ mediaDevices, AudioContext: FakeAudioContext })
  assert.deepEqual(calls.constraints, { audio: true, video: false })
  assert.equal(calls.connectedTo, analyser)
  assert.equal(analyser.fftSize, 512)
  assert.equal(analyser.smoothingTimeConstant, 0.65)
  assert.equal(calls.resumed, 1)

  await monitor.dispose()
  await monitor.dispose()
  assert.equal(calls.trackStops, 1)
  assert.equal(calls.disconnects, 2)
  assert.equal(calls.closed, 1)
})

test('microphone denial does not construct an audio analyzer', async () => {
  let contextsCreated = 0
  class FakeAudioContext {
    constructor() { contextsCreated += 1 }
  }
  const denial = new Error('permission denied')
  const mediaDevices = { getUserMedia: async () => { throw denial } }

  await assert.rejects(
    openLocalMicrophoneAnalyzer({ mediaDevices, AudioContext: FakeAudioContext }),
    error => error === denial,
  )
  assert.equal(contextsCreated, 0)
})

test('an analyzer setup failure stops microphone tracks before surfacing the error', async () => {
  let trackStops = 0
  const failure = new Error('AudioContext construction failed')
  class FailingAudioContext {
    constructor() { throw failure }
  }
  const mediaDevices = {
    getUserMedia: async () => ({ getTracks: () => [{ stop: () => { trackStops += 1 } }] }),
  }

  await assert.rejects(
    openLocalMicrophoneAnalyzer({ mediaDevices, AudioContext: FailingAudioContext }),
    error => error === failure,
  )
  assert.equal(trackStops, 1)
})

test('missing capture APIs fail without trying to access or store audio', async () => {
  await assert.rejects(
    openLocalMicrophoneAnalyzer({ mediaDevices: {}, AudioContext: class FakeAudioContext {} }),
    /capture is unavailable/,
  )
  await assert.rejects(
    openLocalMicrophoneAnalyzer({ mediaDevices: { getUserMedia: async () => ({ getTracks: () => [] }) } }),
    /audio analysis is unavailable/,
  )
})
