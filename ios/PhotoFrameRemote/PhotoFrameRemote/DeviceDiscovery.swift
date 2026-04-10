import Foundation
import Network
import Observation

// MARK: - Discovered Frame

@Observable
final class PhotoFrame: Identifiable {
    let id: String           // mDNS instance name (stable across resolves)
    let name: String         // Human-readable display name
    private(set) var host: String?
    private(set) var port: Int
    private(set) var isReachable: Bool = false

    var api: PhotoFrameAPI? {
        guard let host else { return nil }
        guard let url = URL(string: "http://\(host):\(port)/") else { return nil }
        return PhotoFrameAPI(baseURL: url)
    }

    init(id: String, name: String, host: String? = nil, port: Int = 8080) {
        self.id   = id
        self.name = name
        self.host = host
        self.port = port
    }

    func update(host: String, port: Int, reachable: Bool) {
        self.host        = host
        self.port        = port
        self.isReachable = reachable
    }
}

// MARK: - Discovery

@MainActor
@Observable
final class DeviceDiscovery {
    private(set) var frames: [PhotoFrame] = []
    private(set) var isSearching: Bool    = false

    private var browser: NWBrowser?
    private var connections: [String: NWConnection] = [:]

    init() {
        start()
    }

    func start() {
        guard browser == nil else { return }
        isSearching = true

        let params = NWParameters()
        params.includePeerToPeer = false

        let browser = NWBrowser(for: .bonjourWithTXTRecord(type: "_photoframe._tcp", domain: "local."), using: params)
        self.browser = browser

        browser.stateUpdateHandler = { [weak self] state in
            DispatchQueue.main.async {
                if case .failed = state {
                    self?.isSearching = false
                }
            }
        }

        browser.browseResultsChangedHandler = { [weak self] results, _ in
            DispatchQueue.main.async {
                self?.handleResults(results)
            }
        }

        browser.start(queue: .main)
    }

    func stop() {
        browser?.cancel()
        browser = nil
        connections.values.forEach { $0.cancel() }
        connections.removeAll()
        isSearching = false
    }

    func rescan() {
        stop()
        frames.removeAll()
        start()
    }

    // MARK: - Private

    private func handleResults(_ results: Set<NWBrowser.Result>) {
        // Remove frames no longer advertised
        let activeIDs = Set(results.map { instanceName($0) })
        frames.removeAll { !activeIDs.contains($0.id) }

        // Add or update
        for result in results {
            let id   = instanceName(result)
            let name = displayName(result)

            if frames.first(where: { $0.id == id }) == nil {
                frames.append(PhotoFrame(id: id, name: name))
            }

            resolve(result)
        }

        frames.sort { $0.name < $1.name }
    }

    private func resolve(_ result: NWBrowser.Result) {
        let id = instanceName(result)

        // Resolve the endpoint to get host + port
        let connection = NWConnection(to: result.endpoint, using: .tcp)
        connections[id]?.cancel()
        connections[id] = connection

        connection.stateUpdateHandler = { [weak self] state in
            DispatchQueue.main.async {
                guard let self, let frame = self.frames.first(where: { $0.id == id }) else { return }
                switch state {
                case .ready:
                    if let path = connection.currentPath,
                       let endpoint = path.remoteEndpoint {
                        let (host, port) = self.extractHostPort(from: endpoint, result: result)
                        frame.update(host: host, port: port, reachable: true)
                    }
                case .failed, .cancelled:
                    frame.update(host: frame.host ?? "", port: frame.port, reachable: false)
                default:
                    break
                }
            }
        }

        connection.start(queue: .main)
    }

    private func extractHostPort(from endpoint: NWEndpoint, result: NWBrowser.Result) -> (String, Int) {
        // Try to get the IP from the resolved path endpoint
        if case .hostPort(let host, let port) = endpoint {
            return ("\(host)", Int(port.rawValue))
        }
        // Fallback: parse from the browser result endpoint
        if case .service(let name, _, let domain, _) = result.endpoint {
            return ("\(name).\(domain)", 8080)
        }
        return ("unknown", 8080)
    }

    private func instanceName(_ result: NWBrowser.Result) -> String {
        if case .service(let name, let type_, let domain, _) = result.endpoint {
            return "\(name).\(type_).\(domain)"
        }
        return result.endpoint.debugDescription
    }

    private func displayName(_ result: NWBrowser.Result) -> String {
        if case .service(let name, _, _, _) = result.endpoint {
            // Strip "Photo Frame (" prefix and ")" suffix if present (matches avahi advertisement)
            if name.hasPrefix("Photo Frame (") && name.hasSuffix(")") {
                return String(name.dropFirst(13).dropLast(1))
            }
            return name
        }
        return result.endpoint.debugDescription
    }
}
