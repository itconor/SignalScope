import SwiftUI

struct LaunchLoadingView: View {
    var body: some View {
        ZStack {
            LinearGradient(
                colors: [
                    Color(red: 0.01, green: 0.03, blue: 0.08),
                    Color(red: 0.03, green: 0.09, blue: 0.20)
                ],
                startPoint: .top,
                endPoint: .bottom
            )
            .ignoresSafeArea()

            VStack(spacing: 22) {
                Spacer()

                Image("SignalScopeLaunch")
                    .resizable()
                    .scaledToFit()
                    .frame(maxWidth: 420)
                    .padding(.horizontal, 28)
                    .shadow(color: .black.opacity(0.35), radius: 20, x: 0, y: 10)

                ProgressView()
                    .controlSize(.large)
                    .tint(.white)

                Text("Connecting to hub…")
                    .font(.headline)
                    .foregroundStyle(.white.opacity(0.9))

                Spacer()
            }
            .padding()
        }
    }
}

#Preview {
    LaunchLoadingView()
}
