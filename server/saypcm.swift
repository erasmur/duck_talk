// Persistent macOS speech for DUCK_BACKEND=local: `say` spends ~1.7 s per call starting up, this
// keeps the synthesizer loaded. One request per stdin line, "<voice name>\t<text>"; each answer on
// stdout is a 4-byte little-endian length followed by that many bytes of 24 kHz mono Int16 PCM
// (length 0 when the voice produced nothing).
//   swiftc -O saypcm.swift -o saypcm
import AVFoundation
import Foundation

let rate = Float(ProcessInfo.processInfo.environment["DUCK_AV_RATE"] ?? "") ?? 0.52
let outFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 24000, channels: 1, interleaved: true)!
let synth = AVSpeechSynthesizer()
let stdout = FileHandle.standardOutput

func voice(named name: String) -> AVSpeechSynthesisVoice? {
  let all = AVSpeechSynthesisVoice.speechVoices().filter { $0.name == name }
  // the best installed quality of that name (premium > enhanced > default)
  return all.max { $0.quality.rawValue < $1.quality.rawValue }
}

func reply(_ pcm: Data) {
  var n = UInt32(pcm.count).littleEndian
  stdout.write(Data(bytes: &n, count: 4))
  stdout.write(pcm)
}

func speak(_ text: String, voice name: String, done: @escaping (Data) -> Void) {
  let utterance = AVSpeechUtterance(string: text)
  utterance.voice = voice(named: name)
  utterance.rate = rate
  var pcm = Data()
  var converter: AVAudioConverter?
  var finished = false
  synth.write(utterance) { buffer in
    guard let buf = buffer as? AVAudioPCMBuffer else { return }
    if buf.frameLength == 0 {
      // an empty buffer marks the end of the utterance
      if !finished { finished = true; done(pcm) }
      return
    }
    if converter == nil { converter = AVAudioConverter(from: buf.format, to: outFormat) }
    guard let conv = converter else { return }
    let capacity = AVAudioFrameCount(Double(buf.frameLength) * 24000 / buf.format.sampleRate) + 64
    guard let out = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: capacity) else { return }
    var fed = false
    var error: NSError?
    conv.convert(to: out, error: &error) { _, status in
      if fed { status.pointee = .noDataNow; return nil }
      fed = true
      status.pointee = .haveData
      return buf
    }
    if let samples = out.int16ChannelData, out.frameLength > 0 {
      pcm.append(UnsafeBufferPointer(start: samples[0], count: Int(out.frameLength)))
    }
  }
}

// requests one at a time, answered in order
let lines = DispatchQueue(label: "saypcm.stdin")
lines.async {
  while let line = readLine(strippingNewline: true) {
    let parts = line.split(separator: "\t", maxSplits: 1).map(String.init)
    guard parts.count == 2 else { DispatchQueue.main.sync { reply(Data()) }; continue }
    let sem = DispatchSemaphore(value: 0)
    DispatchQueue.main.async {
      speak(parts[1], voice: parts[0]) { pcm in
        reply(pcm)
        sem.signal()
      }
    }
    sem.wait()
  }
  exit(0)
}
RunLoop.main.run()
