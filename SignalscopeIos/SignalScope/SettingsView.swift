import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var appModel: AppModel
    @AppStorage("SignalScope.baseURL") private var storedBaseURL: String = ""
    @AppStorage("SignalScope.token") private var storedToken: String = ""
    @AppStorage("SignalScope.refreshInterval") private var storedRefresh: Double = 20

    @State private var baseURL: String = ""
    @State private var token: String = ""
    @State private var refresh: Double = 20
    @State private var didLoad = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Connection") {
                    TextField("https://hub.example.com", text: $baseURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()

                    SecureField("API token", text: $token)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }

                Section("Refresh") {
                    VStack(alignment: .leading, spacing: 8) {
                        Slider(value: $refresh, in: 5...120, step: 5)
                        Text("Every \(Int(refresh)) seconds")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }

                Section {
                    Button("Save Settings") {
                        appModel.updateSettings(baseURL: baseURL, token: token, refresh: refresh)
                    }
                }

                Section("Info") {
                    LabeledContent("Status") {
                        Text(appModel.api.baseURL == nil ? "Not configured" : "Configured")
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .background(Theme.backgroundGradient)
            .navigationTitle("Settings")
        }
        .onAppear {
            guard !didLoad else { return }
            baseURL = storedBaseURL
            token = storedToken
            refresh = max(5, storedRefresh)
            didLoad = true
        }
    }
}
