import fs from "fs"
import path from "path"
import { remark } from "remark"
import html from "remark-html"
import * as gfm from "remark-gfm"
import dynamic from "next/dynamic"
import parse from "html-react-parser"
import Footer from "@/components/footer"
import RSSLink from "@/components/rss-link"


const CopyableCode = dynamic(() => import("@/components/CopyableCode"), { ssr: false })

async function getChangelogContent() {
  try {
    const changelogPath = path.join(process.cwd(), "..", "CHANGELOG.md")

    if (!fs.existsSync(changelogPath)) {
      console.error("❌ CHANGELOG.md file not found.")
      return "<p class='text-red-600'>Error: CHANGELOG.md file not found</p>"
    }

    const fileContents = fs.readFileSync(changelogPath, "utf8")

    // Add remark-gfm to support images, tables and other advanced Markdown elements
    const result = await remark()
      .use(gfm.default || gfm) // Safe handling of remark-gfm
      .use(html)
      .process(fileContents)

    return result.toString()
  } catch (error) {
    console.error("❌ Error reading CHANGELOG.md file", error)
    return "<p class='text-red-600'>Error: Could not load changelog content.</p>"
  }
}

// Clean backticks in inline code fragments
function cleanInlineCode(content: string) {
  return content.replace(/<code>(.*?)<\/code>/g, (_, codeContent) => {
    return `<code class="bg-gray-200 text-gray-900 px-1 rounded">${codeContent.replace(/^`|`$/g, "")}</code>`
  })
}

// Wrap code blocks with CopyableCode component
function wrapCodeBlocksWithCopyable(content: string) {
  return parse(content, {
    replace: (domNode: any) => {
      if (domNode.name === "pre" && domNode.children.length > 0) {
        const codeElement = domNode.children.find((child: any) => child.name === "code")
        if (codeElement) {
          const codeContent = codeElement.children[0]?.data?.trim() || ""
          return <CopyableCode code={codeContent} />
        }
      }
    },
  })
}

export default async function ChangelogPage() {
  const changelogContent = await getChangelogContent()
  const cleanedInlineCode = cleanInlineCode(changelogContent) // First clean inline code
  const parsedContent = wrapCodeBlocksWithCopyable(cleanedInlineCode) // Then apply JSX to code blocks

  return (
    <div className="min-h-screen bg-white text-gray-900">
      <div className="container mx-auto px-4 py-16" style={{ maxWidth: "980px" }}>
        {" "}
        {/* Exact adjustment like GitHub */}
        <h1 className="text-4xl font-bold mb-8">Changelog</h1>
        {/* RSS Link Component */}
        <RSSLink />
        <div className="prose max-w-none text-[16px]">{parsedContent}</div> {/* Text adjusted to 16px */}
      </div>
      <Footer />
    </div>
  )
}
