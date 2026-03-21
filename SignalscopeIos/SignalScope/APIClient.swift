import Foundation
import Combine

final class APIClient: ObservableObject {
    @Published var baseURL: URL?
    @Published var token: String = ""

    enum AuthHeaderStyle { case bearer, xApiKey }
    var authStyle: AuthHeaderStyle = .bearer

    init(baseURL: URL? = nil, token: String = "") {
        self.baseURL = baseURL
        self.token = token
    }

    var authHeaders: [String: String] {
        guard !token.isEmpty else { return [:] }
        switch authStyle {
        case .bearer:
            return ["Authorization": "Bearer \(token)"]
        case .xApiKey:
            return ["X-API-Key": token]
        }
    }

    private func makeRequest(path: String) throws -> URLRequest {
        guard let baseURL = baseURL else { throw URLError(.badURL) }
        let url = baseURL.appendingPathComponent(path)
        var req = URLRequest(url: url)
        if !token.isEmpty {
            switch authStyle {
            case .bearer:
                req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            case .xApiKey:
                req.setValue(token, forHTTPHeaderField: "X-API-Key")
            }
        }
        return req
    }

    func fetchChains() async throws -> [ChainSummary] {
        var req = try makeRequest(path: "/api/mobile/chains")
        req.httpMethod = "GET"
        let (data, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        let decoded = try JSONDecoder().decode(ChainsListResponse.self, from: data)
        return decoded.results
    }

    func fetchChainDetail(id: String) async throws -> ChainSummary {
        var req = try makeRequest(path: "/api/mobile/chains/\(id)")
        req.httpMethod = "GET"
        let (data, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        let decoded = try JSONDecoder().decode(ChainDetailResponse.self, from: data)
        return decoded.chain
    }



    func authorizedPlaybackURL(for url: URL) -> URL {
        guard !token.isEmpty, var components = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return url }
        var items = components.queryItems ?? []
        if !items.contains(where: { $0.name == "token" }) {
            items.append(URLQueryItem(name: "token", value: token))
        }
        components.queryItems = items
        return components.url ?? url
    }

    func fetchActiveFaults() async throws -> [ChainSummary] {
        var req = try makeRequest(path: "/api/mobile/active_faults")
        req.httpMethod = "GET"
        let (data, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        let decoded = try JSONDecoder().decode(ActiveFaultsResponse.self, from: data)
        return decoded.results
    }

    /// Polls the specified path until the provided condition closure returns true or the timeout is reached.
    /// - Parameters:
    ///   - path: The API endpoint path to poll.
    ///   - interval: The delay between polls in seconds.
    ///   - timeout: The maximum time to keep polling in seconds.
    ///   - condition: A closure that takes the decoded data and returns true if polling should stop.
    /// - Returns: The decoded response when the condition is met.
    func poll<T: Decodable>(
        path: String,
        interval: TimeInterval = 5,
        timeout: TimeInterval = 60,
        decodeTo type: T.Type,
        condition: @escaping (T) -> Bool
    ) async throws -> T {
        let startTime = Date()
        while true {
            var req = try makeRequest(path: path)
            req.httpMethod = "GET"
            let (data, response) = try await URLSession.shared.data(for: req)
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                throw URLError(.badServerResponse)
            }
            let decoded = try JSONDecoder().decode(T.self, from: data)
            if condition(decoded) {
                return decoded
            }
            if Date().timeIntervalSince(startTime) > timeout {
                throw URLError(.timedOut)
            }
            try await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
        }
    }
}

