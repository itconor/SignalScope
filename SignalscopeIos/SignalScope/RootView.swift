import SwiftUI

struct RootView: View {
    @EnvironmentObject var appModel: AppModel

    var body: some View {
        ZStack {
            ContentView()

            if appModel.isInitialLoad {
                LaunchLoadingView()
                    .transition(.opacity)
                    .zIndex(10)
            }
        }
        .animation(.easeOut(duration: 0.25), value: appModel.isInitialLoad)
    }
}

#Preview {
    RootView()
        .environmentObject(AppModel())
}
