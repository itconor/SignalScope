import SwiftUI

struct ContentView: View {
    @StateObject private var appModel = AppModel()

    var body: some View {
        TabView {
            ChainsListView()
                .tabItem {
                    Image(systemName: "waveform.path.ecg")
                    Text("Chains")
                }

            FaultsView()
                .tabItem {
                    Image(systemName: "exclamationmark.triangle")
                    Text("Faults")
                }
                .badge(appModel.displayedFaults.count)

            SettingsView()
                .tabItem {
                    Image(systemName: "gearshape")
                    Text("Settings")
                }
        }
        .environmentObject(appModel)
        .tint(Theme.brandBlue)
        .preferredColorScheme(.dark)
        .background(Theme.backgroundGradient.ignoresSafeArea())
    }
}

#Preview {
    ContentView()
        .environmentObject(AppModel())
}
