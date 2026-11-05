"use client";

// Plain anchors are deliberate in this client component: they keep the screen
// renderable in tests without the App Router.

import { useEffect, useState, type FormEvent } from "react";

import {
  ApiError,
  fetchProfile,
  saveProfile,
  type ProfileInput,
  type StudentProfile,
} from "@/lib/api";
import { describeApiError } from "@/lib/errors";
import { useHydrated } from "@/lib/hydration";

import { LoadingPanel, RetryPanel, SignedOutPanel } from "./account-panels";

type LoadState = "loading" | "ready" | "new" | "signed-out" | "failed";

type FundingChoice = "" | "yes" | "no";

interface FormValues {
  full_name: string;
  contact_email: string;
  preferred_name: string;
  phone: string;
  institution: string;
  department: string;
  country: string;
  degree_type: string;
  current_year: string;
  expected_graduation: string;
  gpa: string;
  gpa_scale: string;
  coursework: string;
  skills: string;
  research_keywords: string;
  target_countries: string;
  internship_start: string;
  internship_end: string;
  funding_required: FundingChoice;
}

const EMPTY_VALUES: FormValues = {
  full_name: "",
  contact_email: "",
  preferred_name: "",
  phone: "",
  institution: "",
  department: "",
  country: "",
  degree_type: "",
  current_year: "",
  expected_graduation: "",
  gpa: "",
  gpa_scale: "",
  coursework: "",
  skills: "",
  research_keywords: "",
  target_countries: "",
  internship_start: "",
  internship_end: "",
  funding_required: "",
};

const DEGREE_OPTIONS: readonly { value: string; label: string }[] = [
  { value: "", label: "Not specified" },
  { value: "btech", label: "Bachelor of Technology (BTech)" },
  { value: "be", label: "Bachelor of Engineering (BE)" },
  { value: "bs", label: "Bachelor of Science (BS)" },
  { value: "msc", label: "Master of Science (MSc)" },
  { value: "mtech", label: "Master of Technology (MTech)" },
  { value: "dual_degree", label: "Dual degree" },
  { value: "phd", label: "PhD" },
  { value: "other", label: "Other" },
];

function optionalText(value: string | null): string {
  return value ?? "";
}

function numberText(value: number | null): string {
  return value === null ? "" : String(value);
}

function formValuesFromProfile(profile: StudentProfile): FormValues {
  return {
    full_name: profile.full_name,
    contact_email: profile.contact_email,
    preferred_name: optionalText(profile.preferred_name),
    phone: optionalText(profile.phone),
    institution: optionalText(profile.institution),
    department: optionalText(profile.department),
    country: optionalText(profile.country),
    degree_type: optionalText(profile.degree_type),
    current_year: numberText(profile.current_year),
    expected_graduation: optionalText(profile.expected_graduation),
    gpa: numberText(profile.gpa),
    gpa_scale: numberText(profile.gpa_scale),
    coursework: profile.coursework.join(", "),
    skills: profile.skills.join(", "),
    research_keywords: profile.research_keywords.join(", "),
    target_countries: profile.target_countries.join(", "),
    internship_start: optionalText(profile.internship_start),
    internship_end: optionalText(profile.internship_end),
    funding_required:
      profile.funding_required === null ? "" : profile.funding_required ? "yes" : "no",
  };
}

function blankToNull(value: string): string | null {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function numberOrNull(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

function integerOrNull(value: string): number | null {
  const parsed = numberOrNull(value);
  return parsed === null ? null : Math.trunc(parsed);
}

/** "Python, R, lab safety" → ["Python", "R", "lab safety"]; blanks are dropped. */
function splitList(value: string): string[] {
  return value
    .split(/[,\n;]+/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function toInput(values: FormValues): ProfileInput {
  const degree = values.degree_type.trim();
  return {
    full_name: values.full_name.trim(),
    contact_email: values.contact_email.trim(),
    preferred_name: blankToNull(values.preferred_name),
    phone: blankToNull(values.phone),
    institution: blankToNull(values.institution),
    department: blankToNull(values.department),
    country: blankToNull(values.country),
    degree_type: degree === "" ? null : (degree as ProfileInput["degree_type"]),
    current_year: integerOrNull(values.current_year),
    expected_graduation: blankToNull(values.expected_graduation),
    gpa: numberOrNull(values.gpa),
    gpa_scale: numberOrNull(values.gpa_scale),
    coursework: splitList(values.coursework),
    skills: splitList(values.skills),
    research_keywords: splitList(values.research_keywords),
    target_countries: splitList(values.target_countries),
    internship_start: blankToNull(values.internship_start),
    internship_end: blankToNull(values.internship_end),
    funding_required:
      values.funding_required === "" ? null : values.funding_required === "yes",
  };
}

function isSessionLost(failure: unknown): boolean {
  return failure instanceof ApiError && failure.status === 401;
}

interface TextFieldProps {
  readonly id: string;
  readonly label: string;
  readonly value: string;
  readonly onChange: (value: string) => void;
  readonly type?: "text" | "email" | "tel" | "date" | "number";
  readonly hint?: string;
  readonly required?: boolean;
  readonly autoComplete?: string;
  readonly min?: number;
  readonly max?: number;
  readonly step?: string;
}

function TextField({
  id,
  label,
  value,
  onChange,
  type = "text",
  hint,
  required = false,
  autoComplete,
  min,
  max,
  step,
}: TextFieldProps) {
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        name={id}
        type={type}
        value={value}
        required={required}
        autoComplete={autoComplete}
        min={min}
        max={max}
        step={step}
        aria-describedby={hintId}
        onChange={(event) => onChange(event.target.value)}
      />
      {hint ? (
        <p className="hint" id={hintId}>
          {hint}
        </p>
      ) : null}
    </div>
  );
}

interface SelectFieldProps {
  readonly id: string;
  readonly label: string;
  readonly value: string;
  readonly onChange: (value: string) => void;
  readonly hint?: string;
  readonly options: readonly { value: string; label: string }[];
}

function SelectField({ id, label, value, onChange, hint, options }: SelectFieldProps) {
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select
        id={id}
        name={id}
        value={value}
        aria-describedby={hintId}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((option) => (
          <option key={option.value || "blank"} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {hint ? (
        <p className="hint" id={hintId}>
          {hint}
        </p>
      ) : null}
    </div>
  );
}

interface ListFieldProps {
  readonly id: string;
  readonly label: string;
  readonly value: string;
  readonly onChange: (value: string) => void;
  readonly hint: string;
}

function ListField({ id, label, value, onChange, hint }: ListFieldProps) {
  const hintId = `${id}-hint`;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <textarea
        id={id}
        name={id}
        value={value}
        aria-describedby={hintId}
        onChange={(event) => onChange(event.target.value)}
      />
      <p className="hint" id={hintId}>
        {hint}
      </p>
    </div>
  );
}

/**
 * Onboarding and profile editor.
 *
 * `PUT /api/profile` replaces the whole profile, so this form loads what is
 * stored, shows it, and sends every editable field back. A student who has not
 * saved one yet gets `404 profile_missing`, which is a new profile, not an
 * error: the API deliberately avoids inventing a blank row.
 */
export function ProfileForm() {
  const hydrated = useHydrated();
  const [state, setState] = useState<LoadState>("loading");
  const [profile, setProfile] = useState<StudentProfile | null>(null);
  const [values, setValues] = useState<FormValues>(EMPTY_VALUES);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetchProfile()
      .then((stored) => {
        if (cancelled) {
          return;
        }
        setProfile(stored);
        setValues(formValuesFromProfile(stored));
        setState("ready");
      })
      .catch((failure: unknown) => {
        if (cancelled) {
          return;
        }
        if (isSessionLost(failure)) {
          setState("signed-out");
          return;
        }
        if (failure instanceof ApiError && failure.code === "profile_missing") {
          setValues(EMPTY_VALUES);
          setState("new");
          return;
        }
        setError(describeApiError(failure));
        setState("failed");
      });
    return () => {
      cancelled = true;
    };
  }, [reloadToken]);

  function update<K extends keyof FormValues>(key: K, value: FormValues[K]): void {
    setValues((current) => ({ ...current, [key]: value }));
  }

  function retry(): void {
    setError(null);
    setNotice(null);
    setProfile(null);
    setState("loading");
    setReloadToken((token) => token + 1);
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!hydrated) {
      return;
    }
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const saved = await saveProfile(toInput(values));
      setProfile(saved);
      setValues(formValuesFromProfile(saved));
      setState("ready");
      setNotice(
        saved.completeness_missing.length === 0
          ? "Profile saved. Everything the matcher needs is filled in."
          : `Profile saved. It is ${saved.completeness_percent}% complete; add what is still missing when you can.`,
      );
    } catch (failure) {
      if (isSessionLost(failure)) {
        setState("signed-out");
      } else {
        setError(describeApiError(failure));
      }
    } finally {
      setSaving(false);
    }
  }

  if (state === "loading") {
    return <LoadingPanel message="Loading your profile…" />;
  }

  if (state === "signed-out") {
    return <SignedOutPanel intro="Your profile belongs to a signed-in account." />;
  }

  if (state === "failed") {
    return (
      <RetryPanel message={error ?? "Your profile could not be loaded."} onRetry={retry} />
    );
  }

  const canSubmit = hydrated && !saving;

  return (
    <form className="panel" method="post" onSubmit={onSubmit}>
      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p className="alert alert-ok" role="status">
          {notice}
        </p>
      ) : null}

      {profile ? (
        <div className="profile-progress">
          <p>
            <strong>{profile.completeness_percent}% complete.</strong>{" "}
            {profile.completeness_missing.length === 0
              ? "Every field used by matching is filled in."
              : `Still missing: ${profile.completeness_missing.join(", ")}.`}
          </p>
          <progress
            className="progress"
            max={100}
            value={profile.completeness_percent}
            aria-label="Profile completeness"
          />
        </div>
      ) : (
        <p className="hint">
          No profile is saved yet. Only the two identity fields are required to start; the rest
          improves matching and can be added later.
        </p>
      )}

      <fieldset className="form-section">
        <legend>Who you are</legend>
        <div className="field-row">
          <TextField
            id="profile-full-name"
            label="Full name"
            value={values.full_name}
            onChange={(value) => update("full_name", value)}
            autoComplete="name"
            required
          />
          <TextField
            id="profile-contact-email"
            label="Contact email"
            value={values.contact_email}
            onChange={(value) => update("contact_email", value)}
            type="email"
            autoComplete="email"
            required
            hint="An address a professor could reply to. It does not have to be the address you signed in with."
          />
        </div>
        <div className="field-row">
          <TextField
            id="profile-preferred-name"
            label="Preferred name (optional)"
            value={values.preferred_name}
            onChange={(value) => update("preferred_name", value)}
            autoComplete="nickname"
          />
          <TextField
            id="profile-phone"
            label="Phone (optional)"
            value={values.phone}
            onChange={(value) => update("phone", value)}
            type="tel"
            autoComplete="tel"
          />
        </div>
      </fieldset>

      <fieldset className="form-section">
        <legend>Where you study</legend>
        <div className="field-row">
          <TextField
            id="profile-institution"
            label="Institution"
            value={values.institution}
            onChange={(value) => update("institution", value)}
            autoComplete="organization"
          />
          <TextField
            id="profile-department"
            label="Department"
            value={values.department}
            onChange={(value) => update("department", value)}
          />
          <TextField
            id="profile-country"
            label="Country of study"
            value={values.country}
            onChange={(value) => update("country", value)}
            autoComplete="country-name"
          />
        </div>
        <div className="field-row">
          <SelectField
            id="profile-degree-type"
            label="Degree"
            value={values.degree_type}
            onChange={(value) => update("degree_type", value)}
            options={DEGREE_OPTIONS}
          />
          <TextField
            id="profile-current-year"
            label="Current year"
            value={values.current_year}
            onChange={(value) => update("current_year", value)}
            type="number"
            min={1}
            max={8}
            step="1"
            hint="1 to 8."
          />
          <TextField
            id="profile-expected-graduation"
            label="Expected graduation"
            value={values.expected_graduation}
            onChange={(value) => update("expected_graduation", value)}
            type="date"
          />
        </div>
        <div className="field-row">
          <TextField
            id="profile-gpa"
            label="CGPA / GPA"
            value={values.gpa}
            onChange={(value) => update("gpa", value)}
            type="number"
            min={0}
            step="any"
          />
          <TextField
            id="profile-gpa-scale"
            label="Grading scale"
            value={values.gpa_scale}
            onChange={(value) => update("gpa_scale", value)}
            type="number"
            min={0}
            step="any"
            hint="For example 10 or 4. Without it, a grader cannot read the GPA."
          />
        </div>
      </fieldset>

      <fieldset className="form-section">
        <legend>Research fit</legend>
        <p className="hint">
          Lists are comma-separated, and one item cannot contain a comma: the next save would
          split it in two.
        </p>
        <ListField
          id="profile-coursework"
          label="Relevant coursework"
          value={values.coursework}
          onChange={(value) => update("coursework", value)}
          hint="Separate items with commas."
        />
        <ListField
          id="profile-skills"
          label="Technical and laboratory skills"
          value={values.skills}
          onChange={(value) => update("skills", value)}
          hint="Separate items with commas."
        />
        <ListField
          id="profile-research-keywords"
          label="Research interests and keywords"
          value={values.research_keywords}
          onChange={(value) => update("research_keywords", value)}
          hint="Topics, subtopics and methods you want to work on. Separate items with commas."
        />
        <ListField
          id="profile-target-countries"
          label="Preferred countries"
          value={values.target_countries}
          onChange={(value) => update("target_countries", value)}
          hint="Where you would accept an internship. Separate items with commas."
        />
      </fieldset>

      <fieldset className="form-section">
        <legend>Timing and funding</legend>
        <div className="field-row">
          <TextField
            id="profile-internship-start"
            label="Internship start"
            value={values.internship_start}
            onChange={(value) => update("internship_start", value)}
            type="date"
          />
          <TextField
            id="profile-internship-end"
            label="Internship end"
            value={values.internship_end}
            onChange={(value) => update("internship_end", value)}
            type="date"
          />
          <SelectField
            id="profile-funding-required"
            label="Funding required"
            value={values.funding_required}
            onChange={(value) => update("funding_required", value as FundingChoice)}
            options={[
              { value: "", label: "Not specified" },
              { value: "yes", label: "Yes — I need funding or a stipend" },
              { value: "no", label: "No — I can go without funding" },
            ]}
          />
        </div>
      </fieldset>

      <div className="button-row">
        <button className="button" type="submit" disabled={!canSubmit}>
          {saving ? "Saving profile…" : "Save profile"}
        </button>
        <a href="/documents">Upload a CV or transcript</a>
        <a href="/facts">Review extracted facts</a>
      </div>
      <p className="hint">
        Saving replaces the stored profile with exactly what is on this form. Blank optional
        fields are cleared.
      </p>
    </form>
  );
}
