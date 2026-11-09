import Foundation
import Observation

nonisolated enum Lang: String, CaseIterable, Identifiable, Sendable {
    case en, ru
    var id: String { rawValue }
    var title: String { self == .ru ? "Русский" : "English" }
    var locale: Locale { Locale(identifier: self == .ru ? "ru_RU" : "en_US") }
}

/// Current UI language. Views read it through `tr`, so SwiftUI re-renders them when it changes.
@Observable
final class LangState {
    static let shared = LangState()
    var lang: Lang = .en
}

/// Pick the string for the current UI language.
func tr(_ en: String, _ ru: String) -> String {
    LangState.shared.lang == .ru ? ru : en
}
