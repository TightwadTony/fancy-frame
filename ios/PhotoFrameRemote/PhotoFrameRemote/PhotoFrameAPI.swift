import Foundation

// MARK: - Models

struct PhotoFrameInfo: Codable {
    let hostname: String
    let ipAddress: String?
    let uptimeSecs: Double

    enum CodingKeys: String, CodingKey {
        case hostname
        case ipAddress  = "ip_address"
        case uptimeSecs = "uptime_secs"
    }

    var uptimeFormatted: String {
        let total = Int(uptimeSecs)
        let days    = total / 86400
        let hours   = (total % 86400) / 3600
        let minutes = (total % 3600) / 60
        if days > 0 { return "\(days)d \(hours)h \(minutes)m" }
        if hours > 0 { return "\(hours)h \(minutes)m" }
        return "\(minutes)m"
    }
}

struct PhotoFrameConfig: Codable, Equatable {
    var slideSeconds:    Double
    var fadeSeconds:     Double
    var transitions:     [String]
    var kenBurns:        Bool
    var kenBurnsZoomMin: Double
    var kenBurnsZoomMax: Double

    enum CodingKeys: String, CodingKey {
        case slideSeconds    = "slide_seconds"
        case fadeSeconds     = "fade_seconds"
        case transitions
        case kenBurns        = "ken_burns"
        case kenBurnsZoomMin = "ken_burns_zoom_min"
        case kenBurnsZoomMax = "ken_burns_zoom_max"
    }

    static let `default` = PhotoFrameConfig(
        slideSeconds:    25,
        fadeSeconds:     1.5,
        transitions:     ["crossfade", "fade_to_black", "wipe"],
        kenBurns:        true,
        kenBurnsZoomMin: 1.02,
        kenBurnsZoomMax: 1.20
    )
}

// MARK: - API Client

enum APIError: LocalizedError {
    case networkError(Error)
    case httpError(Int)
    case decodingError(Error)
    case validationError([String])

    var errorDescription: String? {
        switch self {
        case .networkError(let e):    return "Network error: \(e.localizedDescription)"
        case .httpError(let code):    return "Server returned HTTP \(code)"
        case .decodingError(let e):   return "Unexpected response: \(e.localizedDescription)"
        case .validationError(let e): return e.joined(separator: "\n")
        }
    }
}

struct PhotoFrameAPI {
    let baseURL: URL

    private var session: URLSession { .shared }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        let url = baseURL.appendingPathComponent(path)
        let (data, response) = try await session.data(from: url)
        guard let http = response as? HTTPURLResponse else { throw APIError.httpError(0) }
        guard (200..<300).contains(http.statusCode) else { throw APIError.httpError(http.statusCode) }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIError.decodingError(error)
        }
    }

    private func patch<T: Decodable>(_ path: String, body: some Encodable) async throws -> T {
        let url = baseURL.appendingPathComponent(path)
        var req = URLRequest(url: url)
        req.httpMethod = "PATCH"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw APIError.httpError(0) }
        if http.statusCode == 422 {
            struct ErrorBody: Decodable { let errors: [String] }
            if let body = try? JSONDecoder().decode(ErrorBody.self, from: data) {
                throw APIError.validationError(body.errors)
            }
        }
        guard (200..<300).contains(http.statusCode) else { throw APIError.httpError(http.statusCode) }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIError.decodingError(error)
        }
    }

    func fetchInfo() async throws -> PhotoFrameInfo {
        try await get("api/info")
    }

    func fetchConfig() async throws -> PhotoFrameConfig {
        try await get("api/config")
    }

    func updateConfig(_ config: PhotoFrameConfig) async throws -> PhotoFrameConfig {
        try await patch("api/config", body: config)
    }

    func restart() async throws {
        let url = baseURL.appendingPathComponent("api/restart")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        let (_, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse,
              (200..<300).contains(http.statusCode) else {
            throw APIError.httpError(0)
        }
    }
}
