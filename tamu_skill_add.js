(async () => {
  // Stop previous scripts.
  window.stopSkills = true;
  window.workdaySkillsRunner?.cancel?.();

  const runner = {
    cancelled: false,
    cancel() {
      this.cancelled = true;
      console.warn("Skills automation cancelled.");
    }
  };

  window.workdaySkillsRunner = runner;

  const skills = [
    "Research",
    "Research Support",
    "Data Collection",
    "Data Entry",
    "Data Analysis",
    "Data Management",
    "Data Validation",
    "Data Quality",
    "Data Coding",
    "Database Management",
    "Records Management",
    "Documentation",
    "Document Review",
    "Quality Assurance",
    "Administrative Support",
    "Microsoft Office",
    "Microsoft Excel",
    "Microsoft Word",
    "Microsoft Outlook",
    "Spreadsheets",
    "Report Writing",
    "Organizational Skills",
    "Time Management",
    "Attention to Detail",
    "Communication",
    "Written Communication",
    "Verbal Communication",
    "Outreach",
    "Customer Service",
    "Stakeholder Engagement",
    "Collaboration",
    "Teamwork",
    "Research Administration",
    "Program Support",
    "Project Coordination",
    "Project Management",
    "Survey Research",
    "Confidentiality",
    "Data Privacy",
    "Information Security",
    "Problem Solving",
    "Python",
    "SQL",
    "Automation",
    "Data Processing"
  ];

  const sleep = ms =>
    new Promise(resolve => setTimeout(resolve, ms));

  const inputSelector = "#skills--skills";
  const getInput = () =>
    document.querySelector(inputSelector);

  const initialInput = getInput();

  if (!initialInput) {
    console.error("Workday Skills input not found.");
    return;
  }

  const widgetId =
    initialInput.getAttribute("data-uxi-multiselect-id");

  // The dropdown is rendered separately under <body>.
  // Workday links it to the field through data-associated-widget.
  function getPopup() {
    return [...document.querySelectorAll(
      '[data-automation-id="responsiveMonikerPrompt"]'
    )].find(el =>
      el.getAttribute("data-associated-widget") === widgetId
    );
  }

  function getFirstResult() {
    const popup = getPopup();
    if (!popup) return null;

    const row = popup.querySelector(
      '[data-automation-id="activeListContainer"] ' +
      '[data-automation-id="menuItem"][aria-posinset="1"]'
    );

    if (!row) return null;

    const leaf = row.querySelector(
      '[data-automation-id="promptLeafNode"]'
    );

    const checkbox = row.querySelector(
      'input[data-automation-id="checkboxPanel"]'
    );

    const label = row.querySelector(
      '[data-automation-id="promptOption"]'
    )?.getAttribute("data-automation-label");

    return { row, leaf, checkbox, label };
  }

  function writeInput(value) {
    const input = getInput();
    if (!input) throw new Error("Input disappeared");

    input.focus();
    input.select();

    // Chrome native text insertion.
    // This generates normal input events when supported.
    document.execCommand("insertText", false, value);

    // Fallback for controlled React inputs.
    if (input.value !== value) {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value"
      ).set;

      setter.call(input, value);

      input.dispatchEvent(
        new InputEvent("input", {
          bubbles: true,
          composed: true,
          inputType: "insertText",
          data: value
        })
      );
    }
  }

  function pressEnter() {
    const input = getInput();
    if (!input) return;

    input.focus();

    for (const type of [
      "keydown",
      "keypress",
      "keyup"
    ]) {
      input.dispatchEvent(
        new KeyboardEvent(type, {
          key: "Enter",
          code: "Enter",
          keyCode: 13,
          which: 13,
          bubbles: true,
          composed: true,
          cancelable: true
        })
      );
    }
  }

  async function waitForResult(timeout = 3000) {
    const start = Date.now();

    while (
      !runner.cancelled &&
      Date.now() - start < timeout
    ) {
      const result = getFirstResult();

      if (result?.leaf && result?.checkbox) {
        return result;
      }

      await sleep(100);
    }

    return null;
  }

  function isChecked(result) {
    if (!result) return false;

    return (
      result.checkbox?.checked === true ||
      result.leaf?.getAttribute(
        "data-automation-checked"
      ) === "Checked"
    );
  }

  function findResultByLabel(label) {
    const popup = getPopup();
    if (!popup) return null;

    const node = [...popup.querySelectorAll(
      '[data-automation-id="promptOption"]'
    )].find(el =>
      el.getAttribute("data-automation-label") === label
    );

    if (!node) return null;

    const leaf = node.closest(
      '[data-automation-id="promptLeafNode"]'
    );

    return {
      leaf,
      checkbox: leaf?.querySelector(
        'input[data-automation-id="checkboxPanel"]'
      )
    };
  }

  const clicked = [];
  const skipped = [];

  for (let index = 0; index < skills.length; index++) {
    if (runner.cancelled) break;

    const skill = skills[index];

    try {
      console.log(
        `[${index + 1}/${skills.length}] Searching: ${skill}`
      );

      // STEP 1: Delete old search and type new skill.
      writeInput(skill);
      await sleep(450);

      // STEP 2: Press Enter.
      pressEnter();
      await sleep(450);

      // STEP 3: Find the Workday popup's first result.
      let result = await waitForResult(2500);

      if (!result) {
        // Fallback: activate the Workday multiselect.
        getInput()?.click();
        await sleep(250);
        pressEnter();
        result = await waitForResult(2500);
      }

      if (runner.cancelled) break;

      if (!result) {
        console.error(
          "STOPPED: Workday did not open results for",
          skill
        );
        skipped.push(skill);
        break;
      }

      const selected = result.label;

      console.log("First result:", selected);

      // STEP 4: Click the actual Workday selectable row.
      if (!isChecked(result)) {
        result.leaf.click();
        await sleep(350);

        // Fallback: click its real checkbox if needed.
        let current = findResultByLabel(selected);

        if (current && !isChecked(current)) {
          current.checkbox?.click();
          await sleep(350);
        }

        current = findResultByLabel(selected);

        if (current && !isChecked(current)) {
          console.error(
            "STOPPED: Click did not select",
            selected
          );
          skipped.push(skill);
          break;
        }
      }

      clicked.push({
        searched: skill,
        selected
      });

      console.log("✓ Clicked:", selected);

      // STEP 5: Clear current search.
      const clearButton = getInput()
        ?.closest('[data-automation-id="multiSelectContainer"]')
        ?.querySelector(
          '[data-automation-id="clearSearchButton"]'
        );

      if (clearButton) clearButton.click();

      writeInput("");

      // STEP 6: Proceed to next skill.
      await sleep(400);

    } catch (error) {
      console.error("STOPPED:", skill, error);
      skipped.push(skill);
      break;
    }
  }

  window.workdaySkillResults = {
    clicked,
    skipped
  };

  console.log("========== FINISHED ==========");
  console.table(clicked);
  console.log("Skipped:", skipped);
})();