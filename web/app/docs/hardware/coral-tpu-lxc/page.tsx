import { Steps } from "@/components/ui/steps"
import CopyableCode from "@/components/CopyableCode"
import Image from "next/image"

export const metadata = {
  title: "Enable Coral TPU in LXC | ProxMenux Documentation",
  description: "Step-by-step guide to enable Google Coral TPU support in an LXC container using ProxMenux.",
}

export default function CoralTPULXC() {
  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="text-3xl font-bold mb-6">Enable Coral TPU in an LXC</h1>

      <p className="mb-4">
        This guide explains how to configure Google Coral TPU support for LXC containers in Proxmox VE using <strong>ProxMenux</strong>.
        Coral TPU provides dedicated AI acceleration, improving inference performance for machine learning applications. It is particularly useful for video surveillance applications with real-time video analysis, such as <a href='https://frigate.video/' target='_blank' className='text-blue-600 hover:underline'>Frigate</a> or <a href='https://www.ispyconnect.com' target='_blank' className='text-blue-600 hover:underline'>Agent DVR</a> or <a href='https://blueirissoftware.com/' target='_blank' className='text-blue-600 hover:underline'>Blue Iris</a> using <a href='https://www.codeproject.com/ai/index.aspx' target='_blank' className='text-blue-600 hover:underline'>CodeProject.AI</a>.
      </p>

      <h2 className="text-2xl font-semibold mt-8 mb-4">Overview</h2>
      <p className="mb-4">The script automates the complete configuration of Coral TPU support in LXC containers, including USB and M.2 variants. It applies Proxmox-specific container settings, manages device passthrough permissions, and installs required drivers both on the host and inside the container.</p>
      <p className="mb-4">The USB variant uses a persistent mapping based on <code>/dev/coral</code> via <code>udev</code> rules, avoiding reliance on dynamic USB paths like <code>/dev/bus/usb/*</code>. This ensures consistent device assignment across reboots and hardware reordering.</p>
      <p className="mb-4">The M.2 version is detected automatically and configured only if present.</p>

      <h2 className="text-2xl font-semibold mt-8 mb-4">Implementation Steps</h2>
      <Steps>
        <Steps.Step title="Select an LXC Container">
          <p>The script lists available LXC containers and prompts for selection.</p>
        </Steps.Step>
        <Steps.Step title="Modify Container Configuration">
          <p>The script applies necessary changes to enable Coral TPU:</p>
          <ul className="list-disc pl-6 space-y-1 mt-2">
            <li>Switches the container to privileged mode if required.</li>
            <li>Enables nesting to allow GPU and TPU usage.</li>
            <li>Sets device permissions for TPU and iGPU.</li>
            <li>Configures proper device mounts.</li>
          </ul>
          <CopyableCode
            code={`# Coral USB persistent passthrough example:
/etc/udev/rules.d/99-coral-usb.rules
SUBSYSTEM=="usb", ATTRS{idVendor}=="18d1", ATTRS{idProduct}=="9302", SYMLINK+="coral", MODE="0666"

# LXC config:
lxc.cgroup2.devices.allow: c 189:* rwm
lxc.mount.entry: /dev/coral dev/coral none bind,optional,create=file`}
            className="my-4"
          />
          <CopyableCode
            code={`# Coral M.2 passthrough example (automatically added if detected):
lxc.cgroup2.devices.allow: c 245:0 rwm
lxc.mount.entry: /dev/apex_0 dev/apex_0 none bind,optional,create=file`}
            className="my-4"
          />
        </Steps.Step>
        <Steps.Step title="Install Required Drivers">
          <p>The script installs the necessary components inside the container:</p>
          <ul className="list-disc pl-6 space-y-1 mt-2">
            <li>GPU drivers:</li>
            <ul className="list-disc pl-10">
              <li><code>va-driver-all</code></li>
              <li><code>ocl-icd-libopencl1</code></li>
              <li><code>intel-opencl-icd</code></li>
              <li><code>vainfo</code></li>
              <li><code>intel-gpu-tools</code></li>
            </ul>
            <li>Coral TPU dependencies:</li>
            <ul className="list-disc pl-10">
              <li><code>python3</code></li>
              <li><code>python3-pip</code></li>
              <li><code>python3-venv</code></li>
              <li><code>gnupg</code></li>
              <li><code>curl</code></li>
            </ul>
            <li>Coral TPU drivers:</li>
            <ul className="list-disc pl-10">
              <li><code>libedgetpu1-std</code> (standard performance)</li>
              <li><code>libedgetpu1-max</code> (maximum performance, optional)</li>
            </ul>
          </ul>
        </Steps.Step>
        <Steps.Step title="Select Coral TPU Driver Version">
          <p>If a Coral M.2 device is detected, the script prompts the user to select:</p>
          <ul className="list-disc pl-6 space-y-1 mt-2">
            <li><strong>Standard mode</strong> - balanced performance.</li>
            <li><strong>Maximum performance mode</strong> - higher speed, increased power usage.</li>
          </ul>
        </Steps.Step>
      </Steps>

      <h2 className="text-2xl font-semibold mt-8 mb-4">Expected Results</h2>
      <ul className="list-disc pl-6 space-y-2 mb-6">
        <li>The selected container is correctly configured for TPU and iGPU usage.</li>
        <li>Required drivers and dependencies are installed inside the container.</li>
        <li>The container will restart as needed during the process.</li>
        <li>After completion, applications inside the container can utilize Coral TPU acceleration.</li>
      </ul>

      <h2 className="text-2xl font-semibold mt-8 mb-4">Important Considerations</h2>
      <ul className="list-disc pl-6 space-y-2 mb-6">
        <li>The script supports both USB and M.2 Coral TPU devices.</li>
        <li>The Proxmox host must have the required Coral TPU and Intel GPU drivers installed.</li>
        <li>Additional application-specific configurations may be required inside the container.</li>
        <li>Coral USB passthrough uses a persistent device alias <code>/dev/coral</code> created by a udev rule. This improves stability and avoids issues with changing USB port identifiers.</li>
        <li>Coral M.2 devices are detected dynamically using <code>lspci</code> and configured only if present.</li>
      </ul>
    </div>
  )
}
